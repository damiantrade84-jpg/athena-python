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
import warnings
from datetime import datetime, timezone
from typing import Dict, List, Optional

from config import CONFIG
from regime import detect_regime

log = logging.getLogger("athena")

# ── Constants ─────────────────────────────────────────────────────────────────

_CRYPTO_COT_PAIRS = {"BTC/USDT", "ETH/USDT"}


def _resolve_class_keyed(mapping, score_group: str | None, asset_type: str, default):
    """Resolve a per-class config block by score_group → asset_type → 'default'.

    Lets PAIR_PROFILES.score_group + per-subgroup config entries take effect
    where previously only pair["type"] was honoured.  E.g. a stock-typed pair
    routed to score_group "precious_trackers" can now pull commodity-style
    RSI bounds without touching its asset_type.
    """
    if not isinstance(mapping, dict):
        return default
    if score_group and score_group in mapping:
        return mapping[score_group]
    if asset_type in mapping:
        return mapping[asset_type]
    if "default" in mapping:
        return mapping["default"]
    return default


def _float_cfg(value, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _resolve_pair_score_group(pair: dict) -> str | None:
    try:
        from scoring import get_pair_score_group
        return get_pair_score_group(pair)
    except Exception:
        return pair.get("score_group")


def _engine_a_group_adjustments_enabled() -> bool:
    return bool(CONFIG.get("ENGINE_A_SCORE_GROUP_ADJUSTMENTS_ENABLED", False))


def _resolve_factor_weights(score_group: str | None, asset_type: str) -> dict:
    weights = {
        "momentum": _float_cfg(CONFIG.get("FACTOR_MOMENTUM_WEIGHT"), _MOMENTUM_WEIGHT_DEFAULT),
        "addon": _float_cfg(CONFIG.get("FACTOR_ADDON_WEIGHT"), _ADDON_WEIGHT_DEFAULT),
        "base": _float_cfg(CONFIG.get("FACTOR_BASE_WEIGHT"), _BASE_WEIGHT_DEFAULT),
    }
    if not _engine_a_group_adjustments_enabled():
        return weights

    keyed = CONFIG.get("ENGINE_A_FACTOR_WEIGHTS_BY_CLASS") or {}
    overrides = _resolve_class_keyed(keyed, score_group, asset_type, {})
    if isinstance(overrides, dict):
        for key in ("momentum", "addon", "base"):
            if key in overrides:
                weights[key] = _float_cfg(overrides.get(key), weights[key])
    return weights


def _resolve_directional_ramp(asset_type: str, score_group: str | None) -> tuple[float, float]:
    if asset_type == "crypto":
        min_directional = _float_cfg(CONFIG.get("FACTOR_MIN_DIRECTIONAL_CRYPTO"), 0.15)
        soft_span = _float_cfg(CONFIG.get("FACTOR_DIRECTIONAL_SOFT_SPAN_CRYPTO"), 0.30)
    else:
        min_directional = _float_cfg(CONFIG.get("FACTOR_MIN_DIRECTIONAL"), 0.25)
        soft_span = _float_cfg(CONFIG.get("FACTOR_DIRECTIONAL_SOFT_SPAN"), 0.20)

    if not _engine_a_group_adjustments_enabled():
        return min_directional, soft_span

    keyed = CONFIG.get("ENGINE_A_DIRECTIONAL_RAMP_BY_CLASS") or {}
    overrides = _resolve_class_keyed(keyed, score_group, asset_type, {})
    if isinstance(overrides, dict):
        min_directional = _float_cfg(overrides.get("min_directional"), min_directional)
        soft_span = _float_cfg(overrides.get("soft_span"), soft_span)
    return min_directional, soft_span


def _resolve_addon_split(score_group: str | None, asset_type: str) -> float:
    split = _float_cfg(CONFIG.get("ADDON_UNSUPPORTED_SPLIT_TO_BASE"), 0.0)
    if _engine_a_group_adjustments_enabled():
        keyed = CONFIG.get("ENGINE_A_ADDON_UNSUPPORTED_SPLIT_BY_CLASS") or {}
        split = _float_cfg(_resolve_class_keyed(keyed, score_group, asset_type, split), split)
    return max(0.0, min(1.0, split))


def _resolve_conviction_floor(score_group: str | None, asset_type: str, default: float) -> float:
    floor = default
    if _engine_a_group_adjustments_enabled():
        keyed = CONFIG.get("ENGINE_A_CONVICTION_FLOOR_BY_CLASS") or {}
        floor = _float_cfg(_resolve_class_keyed(keyed, score_group, asset_type, floor), floor)
    return max(0.0, min(1.0, floor))


def _resolve_research_lab_profile(
    base_cfg: dict,
    score_group: str | None,
    asset_type: str,
) -> tuple[dict, str, bool]:
    """Resolve optional class-keyed Research Lab settings.

    Research Lab remains globally gated by ENGINE_A_RESEARCH_LAB_FACTORS.ENABLED.
    The per-class map only applies when Engine A score-group adjustments are on,
    keeping the historical universal Research Lab behavior as the safe default.
    """
    resolved = dict(base_cfg or {})
    if not _engine_a_group_adjustments_enabled():
        return resolved, "universal", False

    keyed = CONFIG.get("ENGINE_A_RESEARCH_LAB_FACTORS_BY_CLASS") or {}
    if not isinstance(keyed, dict):
        return resolved, "universal", False

    override = None
    source = "universal"
    if score_group and score_group in keyed:
        override = keyed.get(score_group)
        source = f"score_group:{score_group}"
    elif asset_type in keyed:
        override = keyed.get(asset_type)
        source = f"asset_type:{asset_type}"
    elif "default" in keyed:
        override = keyed.get("default")
        source = "default"

    if isinstance(override, dict):
        for key in ("BONUS", "PENALTY", "MAX_ABS", "FACTORS"):
            if key in override:
                resolved[key] = override[key]
        return resolved, source, True
    return resolved, source, False

# ADX gate thresholds (Wilder 1978 standard) — tunable via config.yaml FACTOR_ADX_*
# NOTE: Read lazily inside _adx_gate() so config reloads take effect without restart.
_ADX_HARD_FAIL_DEFAULT = 15.0    # below this → dead market, abort
_ADX_SOFT_MULT_DEFAULT = 0.65    # multiplier in soft zone

# Track whether we've emitted the fallback warning (per-process, not per-call)
_adx_fallback_warned = False

# Session multiplier defaults (forex only — read lazily in _session_multiplier)
_SESSION_CORE_MULT_DEFAULT = 1.00
_SESSION_SHOULDER_MULT_DEFAULT = 0.90
_SESSION_OFF_MULT_DEFAULT = 0.75

# Volatility scaler defaults (Stage 3.4: replaces session multiplier)
_VOLATILITY_SCALER_ATR_PCT_LOW = 0.005   # 0.5% ATR — low vol
_VOLATILITY_SCALER_ATR_PCT_HIGH = 0.025  # 2.5% ATR — high vol
_VOLATILITY_SCALER_MULT_LOW = 1.15       # reward precision in low vol
_VOLATILITY_SCALER_MULT_HIGH = 0.85      # penalise noise in high vol

# Addon contribution bounds
# Stage 1.4: aligned with research lab MAX_ABS (0.20) so clamp order is deterministic.
# Research lab computes aggregate → single clamp to [-0.15, +0.20].
_ADDON_CONFIRM = float(CONFIG.get("FACTOR_ADDON_CONFIRM_MAX", 0.20))    # confirming the trend direction
_ADDON_NEUTRAL = 0.00    # data missing or neutral
_ADDON_AGAINST = float(CONFIG.get("FACTOR_ADDON_AGAINST_MIN", -0.15))   # actively opposing direction

# Final score formula weight defaults — read lazily inside compute_factor_scores
_MOMENTUM_WEIGHT_DEFAULT = 0.50
_ADDON_WEIGHT_DEFAULT = 0.30
_BASE_WEIGHT_DEFAULT = 0.20
_CONVICTION_FLOOR_DEFAULT = 0.20
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

def _ema_cross_confirmed(current_snap: dict, prev_snap: dict, fast_key: str, slow_key: str) -> float | None:
    """Stage 3.3: EMA vote hysteresis — require 2-bar confirmation for a cross.

    Returns +1.0 (LONG), -1.0 (SHORT), or None (indeterminate / no confirmation).
    A cross is confirmed only if:
      - Current bar: fast > slow (or fast < slow)
      - Previous bar: same relationship holds (not a fresh cross)
    This filters out whipsaw noise from single-bar EMA breaches.
    """
    fast_cur = current_snap.get(fast_key)
    slow_cur = current_snap.get(slow_key)
    fast_prev = prev_snap.get(fast_key) if isinstance(prev_snap, dict) else None
    slow_prev = prev_snap.get(slow_key) if isinstance(prev_snap, dict) else None

    if fast_cur is None or slow_cur is None or slow_cur == 0:
        return None
    try:
        fast_cur = float(fast_cur)
        slow_cur = float(slow_cur)
    except (TypeError, ValueError):
        return None

    cur_long = fast_cur > slow_cur
    cur_short = fast_cur < slow_cur

    # If previous bar data available, require consistency
    if fast_prev is not None and slow_prev is not None:
        try:
            fast_prev = float(fast_prev)
            slow_prev = float(slow_prev)
        except (TypeError, ValueError):
            fast_prev = None
            slow_prev = None

    if fast_prev is not None and slow_prev is not None:
        prev_long = fast_prev > slow_prev
        prev_short = fast_prev < slow_prev
        if cur_long and prev_long:
            return 1.0
        if cur_short and prev_short:
            return -1.0
        # Fresh cross — wait one more bar for confirmation
        return None

    # No previous data — allow current bar only if gap is significant (>0.1%)
    _gap = abs(fast_cur - slow_cur) / abs(slow_cur) if slow_cur != 0 else 0
    if _gap > 0.001:
        return 1.0 if cur_long else (-1.0 if cur_short else None)
    return None


def _coherent_trend_score(
    d1_snap: dict, h4_snap: dict, h1_snap: dict, asset_type: str,
    d1_prev: dict | None = None, h4_prev: dict | None = None, h1_prev: dict | None = None,
    score_group: str | None = None,
) -> tuple:
    """Multi-TF EMA alignment trend score.

    Returns (trend_score, direction, trend_detail).
    trend_score  : float in [-3.0, +3.0]; 0.0 means indeterminate.
    direction    : "LONG" | "SHORT" | None
    trend_detail : dict with per-TF votes and coherence ratio.

    D1 weight 0.50 (tide), H4 weight 0.30 (momentum), H1 weight 0.20 (entry).
    All 3 aligned → ±3.0; 2-of-3 dominant → ±(0.35+0.65*coherence)*3.0.
    All split (1.5 LONG / 1.5 SHORT with equal weights) → 0.0, direction=None.

    Stage 3.3: EMA hysteresis — 2-bar confirmation required. Pass previous-bar
    snaps as d1_prev/h4_prev/h1_prev to enable; missing prev = gap check fallback.
    """
    tf_weights_raw = CONFIG.get("INDICATOR_WEIGHTS", {}).get("trend", {})
    tf_weights = _resolve_class_keyed(tf_weights_raw, score_group, asset_type, {})
    if not isinstance(tf_weights, dict):
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
    d1_sign = _ema_cross_confirmed(d1_snap, d1_prev, "ema21", "ema200")
    if d1_sign is not None:
        votes.append(("d1_ema_trend", d1_sign, _w("d1_ema_trend")))
        detail["d1"] = "LONG" if d1_sign > 0 else "SHORT"
    elif d1_snap.get("ema21") is not None and d1_snap.get("ema200") is None:
        detail["d1_ema200_missing"] = True
    elif d1_snap.get("ema21") is not None and d1_snap.get("ema200") is not None:
        detail["d1_hysteresis_pending"] = True

    # H4 — momentum confirmation (EMA21 vs EMA50)
    h4_sign = _ema_cross_confirmed(h4_snap, h4_prev, "ema21", "ema50")
    if h4_sign is not None:
        votes.append(("h4_ema_trend", h4_sign, _w("h4_ema_trend")))
        detail["h4"] = "LONG" if h4_sign > 0 else "SHORT"
    elif h4_snap.get("ema21") is not None and h4_snap.get("ema50") is not None:
        detail["h4_hysteresis_pending"] = True

    # H1 — entry quality (EMA21 vs EMA50)
    h1_sign = _ema_cross_confirmed(h1_snap, h1_prev, "ema21", "ema50")
    if h1_sign is not None:
        votes.append(("ema_trend", h1_sign, _w("ema_trend")))
        detail["h1"] = "LONG" if h1_sign > 0 else "SHORT"
    elif h1_snap.get("ema21") is not None and h1_snap.get("ema50") is not None:
        detail["h1_hysteresis_pending"] = True

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
    # coherence_ratio floor removed (was 0.5). A tied or near-tied vote should
    # produce near-zero magnitude, not a "moderate" score that amplifies noise.
    # Configurable via COHERENCE_RATIO_FLOOR for experiments; default = 0.0.
    _coh_floor = float(CONFIG.get("COHERENCE_RATIO_FLOOR", 0.0))
    _coh_floor = max(0.0, min(1.0, _coh_floor))
    coherence_ratio = max(_coh_floor, min(1.0, dominant_w / total_w))
    agreement_count = sum(1 for _, d, _ in votes if d == dominant_sign)
    # Scale by TF coverage so a single available TF cannot produce a full 3.0 score.
    # D1 only → max 1.0; D1+H4 → max 2.0; all three → max 3.0.
    # FIX 5: Single-vote trend weight scaling — scale coverage by relative weight
    # so D1-only > H4-only > H1-only.
    active_votes = len(votes)
    if active_votes == 1:
        max_single_weight = max(
            _w("d1_ema_trend"),
            _w("h4_ema_trend"),
            _w("ema_trend"),
        )
        if max_single_weight <= 0:
            max_single_weight = dominant_w
        dominant_weight = dominant_w
        _tf_coverage = (1.0 / 3.0) * (dominant_weight / max_single_weight)
    else:
        _tf_coverage = active_votes / 3.0
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


def _previous_indicator_snap(candles: list | None) -> dict | None:
    """Return the prior confirmed indicator snapshot for EMA hysteresis."""
    if not isinstance(candles, list) or len(candles) < 2:
        return None
    try:
        from indicators import calc_indicators

        prev_indicators = calc_indicators(candles[:-1])
        prev_snap = prev_indicators.get("snap") if isinstance(prev_indicators, dict) else None
        return prev_snap if isinstance(prev_snap, dict) else None
    except Exception as exc:
        log.debug("[EA2] previous indicator snapshot unavailable: %s", exc)
        return None


def _confidence_filtered_indicators(
    d1_snap: dict,
    h4_snap: dict,
    h1_snap: dict,
    d1_prev: Optional[dict],
    h4_prev: Optional[dict],
    h1_prev: Optional[dict],
    _direction: str,
    volume_ratio: Optional[float],
    funding_rate: Optional[float],
    mean_rev_detail: dict,
) -> Dict[str, Optional[float]]:
    """Populate confidence_engine.indicator_agreement inputs (mostly [-1, 1])."""
    out: Dict[str, Optional[float]] = {}

    d1_sign = _ema_cross_confirmed(d1_snap, d1_prev, "ema21", "ema200")
    if d1_sign is not None:
        out["d1_ema_trend"] = float(d1_sign)
    h4_t_sign = _ema_cross_confirmed(h4_snap, h4_prev, "ema21", "ema50")
    if h4_t_sign is not None:
        out["h4_ema_trend"] = float(h4_t_sign)
    h1_t_sign = _ema_cross_confirmed(h1_snap, h1_prev, "ema21", "ema50")
    if h1_t_sign is not None:
        out["ema_trend"] = float(h1_t_sign)

    rsi = h4_snap.get("rsi")
    if rsi is not None:
        try:
            out["rsi_z"] = max(-1.0, min(1.0, (float(rsi) - 50.0) / 50.0))
        except (TypeError, ValueError):
            pass

    macd_hist = h4_snap.get("macdHist")
    if macd_hist is not None:
        try:
            mh = float(macd_hist)
            out["macdLine_z"] = max(-1.0, min(1.0, math.tanh(mh * 5.0)))
        except (TypeError, ValueError):
            pass

    adx = h4_snap.get("adx")
    if adx is not None:
        try:
            adx_f = float(adx)
            out["adx_z"] = max(-1.0, min(1.0, (adx_f - 25.0) / 35.0))
        except (TypeError, ValueError):
            pass

    close = h4_snap.get("close")
    atr = h4_snap.get("atr")
    if close is not None and atr is not None:
        try:
            c = float(close)
            a = float(atr)
            if c > 0 and a >= 0:
                out["atr_z"] = max(-1.0, min(1.0, (a / c) * 80.0))
        except (TypeError, ValueError):
            pass

    bb_pct = h4_snap.get("bbWidth_pct")
    if bb_pct is None:
        bb_pct = h4_snap.get("bb_width_pct")
    if bb_pct is not None:
        try:
            out["bbWidth_z"] = max(-1.0, min(1.0, (float(bb_pct) - 50.0) / 50.0))
        except (TypeError, ValueError):
            pass

    if isinstance(volume_ratio, (int, float)) and volume_ratio > 0:
        out["volume_ratio"] = float(min(3.0, volume_ratio))

    if isinstance(funding_rate, (int, float)):
        try:
            fr = float(funding_rate)
            out["funding_rate"] = max(-1.0, min(1.0, math.tanh(fr * 120.0)))
        except (TypeError, ValueError):
            pass

    if mean_rev_detail.get("enabled") and mean_rev_detail.get("z_score") is not None:
        try:
            z = float(mean_rev_detail["z_score"])
            out["fib_proximity"] = max(-1.0, min(1.0, z / 3.0))
        except (TypeError, ValueError):
            pass

    for key in (
        "order_book_imbalance",
        "liquidity_wall_detection",
        "orderflow_delta",
        "liquidity_pressure",
        "volume_momentum_spread",
    ):
        v = h4_snap.get(key)
        if v is None:
            continue
        try:
            fv = float(v)
        except (TypeError, ValueError):
            continue
        if fv == 0.0:
            continue
        out[key] = max(-1.0, min(1.0, fv))

    return out


# ── Factor 2: Momentum quality (RSI + MACD confirmation) ─────────────────────

def _stochastic_rsi_modifier(
    h4_candles: list,
    direction: str,
    asset_type: str,
) -> float:
    """Stochastic RSI momentum modifier (experimental, config-gated).

    Returns a small adjustment in [-0.1, +0.1] based on Stochastic RSI signals.
    StochRSI > 80 = overbought (penalise LONGs, confirm SHORTs)
    StochRSI < 20 = oversold (confirm LONGs, penalise SHORTs)
    """
    cfg = CONFIG.get("ENGINE_A_STOCHASTIC_RSI", {}) or {}
    if not cfg.get("ENABLED", False):
        return 0.0

    rsi_period = int(cfg.get("RSI_PERIOD", 14))
    stoch_period = int(cfg.get("STOCH_PERIOD", 14))
    k_smooth = int(cfg.get("K_SMOOTH", 3))
    d_smooth = int(cfg.get("D_SMOOTH", 3))
    overbought = float(cfg.get("OVERBOUGHT", 80))
    oversold = float(cfg.get("OVERSOLD", 20))

    if not h4_candles or len(h4_candles) < rsi_period + stoch_period + k_smooth + d_smooth:
        return 0.0

    try:
        from indicators import calc_stochastic_rsi
        stoch_rsi = calc_stochastic_rsi(h4_candles, rsi_period, stoch_period, k_smooth, d_smooth)
        k_values = stoch_rsi.get("k", [])
        if not k_values or k_values[-1] is None:
            return 0.0

        k = float(k_values[-1])
        is_long = direction == "LONG"

        # StochRSI overbought/oversold signals
        if is_long:
            if k < oversold:
                return 0.1  # Oversold = good for LONGs
            elif k > overbought:
                return -0.1  # Overbought = bad for LONGs
        else:
            if k > overbought:
                return 0.1  # Overbought = good for SHORTs
            elif k < oversold:
                return -0.1  # Oversold = bad for SHORTs

        return 0.0
    except Exception as exc:
        log.debug("[EA2] Stochastic RSI modifier error: %s", exc)
        return 0.0


def _momentum_quality(
    h4_snap: dict, direction: str, asset_type: str,
    score_group: str | None = None,
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
    rsi_bounds = _resolve_class_keyed(
        CONFIG.get("RSI_BOUNDS", {}), score_group, asset_type, {"ob": 70, "os": 30}
    )
    if not isinstance(rsi_bounds, dict):
        rsi_bounds = {"ob": 70, "os": 30}
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

    # Stage 2.7: Divergence detection as disqualification.
    # If price makes higher highs but RSI/MACD makes lower highs (bearish div),
    # or price makes lower lows but RSI/MACD makes higher lows (bullish div),
    # momentum quality is zeroed — divergence precedes reversals.
    def _detect_divergence(price_vals: list, ind_vals: list, dir_: str) -> bool:
        if len(price_vals) < 3 or len(ind_vals) < 3:
            return False
        if dir_ == "LONG":
            # Bearish divergence: higher price high, lower indicator high
            return price_vals[-1] > price_vals[-2] and ind_vals[-1] < ind_vals[-2]
        else:
            # Bullish divergence: lower price low, higher indicator low
            return price_vals[-1] < price_vals[-2] and ind_vals[-1] > ind_vals[-2]

    _price_swings = h4_snap.get("price_swings", [])
    _rsi_swings = h4_snap.get("rsi_swings", [])
    _macd_hist_swings = h4_snap.get("macd_hist_swings", [])
    _divergence_detected = (
        _detect_divergence(_price_swings, _rsi_swings, direction)
        or _detect_divergence(_price_swings, _macd_hist_swings, direction)
    )

    # MACD histogram score — aligned confirms, opposing penalises strongly.
    # Penalty increased from -0.15 to -0.50 (2026-04-30 audit): the old value
    # was too weak — with RSI weight 0.6 and MACD weight 0.4, opposing MACD
    # only reduced the weighted sum by 0.06, making MACD divergence irrelevant.
    macd_score = 0.0
    macd_hist = h4_snap.get("macdHist")
    if macd_hist is None:
        macd_hist = h4_snap.get("macd_hist")
    if macd_hist is not None:
        try:
            hist = float(macd_hist)
            if is_long and hist > 0:
                macd_score = 0.50
            elif not is_long and hist < 0:
                macd_score = 0.50
            elif (is_long and hist < 0) or (not is_long and hist > 0):
                macd_score = -0.50
        except (TypeError, ValueError):
            pass

    volume_momentum_score = 0.0
    if _engine_a_group_adjustments_enabled():
        try:
            vol_spread = float(h4_snap.get("volume_momentum_spread", 0.0) or 0.0)
            vol_spread = max(-1.0, min(1.0, vol_spread))
            if (is_long and vol_spread > 0) or ((not is_long) and vol_spread < 0):
                volume_momentum_score = 0.50 * abs(vol_spread)
            elif (is_long and vol_spread < 0) or ((not is_long) and vol_spread > 0):
                volume_momentum_score = -0.50 * abs(vol_spread)
        except (TypeError, ValueError):
            pass

    # If divergence detected, override momentum to zero regardless of indicator values
    if _divergence_detected:
        return 0.0

    # Per-indicator weights from config
    ind_weights = CONFIG.get("INDICATOR_WEIGHTS", {}).get("momentum", {})
    if isinstance(ind_weights, dict) and any(isinstance(v, dict) for v in ind_weights.values()):
        ind_weights = _resolve_class_keyed(ind_weights, score_group, asset_type, {})
    rsi_w = float(ind_weights.get("rsi_z", 0.6)) if isinstance(ind_weights, dict) else 0.6
    macd_w = float(ind_weights.get("macdLine_z", 0.4)) if isinstance(ind_weights, dict) else 0.4
    volume_w = 0.0
    if (
        _engine_a_group_adjustments_enabled()
        and isinstance(ind_weights, dict)
        and abs(volume_momentum_score) > 1e-12
    ):
        volume_w = float(ind_weights.get("volume_momentum_spread", 0.0) or 0.0)
    total_w = rsi_w + macd_w + volume_w
    if total_w <= 0:
        total_w = 1.0

    raw = (
        rsi_score * rsi_w
        + macd_score * macd_w
        + volume_momentum_score * volume_w
    ) / total_w
    # Rescale to true [0, 1] range. The raw weighted sum maxes at 0.50
    # (both RSI and MACD at +0.50), so we divide by 0.50 to map 0.50 → 1.0.
    # Worst case: RSI=-0.25, MACD=-0.50 → raw = -0.35 → rescaled = -0.70 → clamped to 0.
    # This ensures mom_quality can actually reach 1.0 when both indicators confirm.
    _max_raw = 0.50  # theoretical max of weighted sum
    rescaled = raw / _max_raw if _max_raw != 0 else 0.0
    return max(0.0, min(1.0, rescaled))


def _research_lab_candidate_addon(
    pair: dict,
    direction: str,
    h4_candles: list,
    d1_candles: list,
    asset_type: str,
    score_group: str | None = None,
) -> tuple[float, dict]:
    """Research Lab candidate factor, config-gated and bounded.

    Stage 2.6: Defaults to 3 universal factors for all asset classes:
      obv_divergence, bollinger_touch, price_momentum
    Per-class factor lists are optional and gated by Engine A class adjustments.
    """
    cfg = CONFIG.get("ENGINE_A_RESEARCH_LAB_FACTORS", {}) or {}
    if not cfg.get("ENABLED", False):
        return 0.0, {"enabled": False}
    cfg, research_source, class_adjusted = _resolve_research_lab_profile(
        cfg, score_group, asset_type
    )

    default_candles = d1_candles if len(d1_candles or []) >= 60 else h4_candles
    if not default_candles or len(default_candles) < 60:
        return 0.0, {
            "enabled": True,
            "applied": False,
            "reason": "insufficient_candles",
        }

    bonus = float(cfg.get("BONUS", _RESEARCH_BONUS_DEFAULT))
    penalty = float(cfg.get("PENALTY", _RESEARCH_PENALTY_DEFAULT))
    max_abs = abs(float(cfg.get("MAX_ABS", 0.20)))

    # Stage 2.6: Universal factor list — same for all asset classes
    _UNIVERSAL_RL_FACTORS = ["obv_divergence", "bollinger_touch", "price_momentum"]
    allowed = cfg.get("FACTORS", _UNIVERSAL_RL_FACTORS)

    components: dict[str, dict] = {}
    total = 0.0

    for name in allowed:
        factor_name = str(name)
        candles = h4_candles if factor_name == "stochastic_cross" else default_candles
        val, detail = _research_factor_value(
            factor_name,
            direction,
            candles,
            bonus,
            penalty,
            cfg=cfg,
            pair=pair,
            asset_type=asset_type,
        )
        components[str(name)] = detail
        total += val

    total = max(-max_abs, min(max_abs, total))
    return total, {
        "enabled": True,
        "score_group": (score_group if class_adjusted else "universal"),
        "resolved_score_group": score_group,
        "profile_source": research_source,
        "class_adjusted": class_adjusted,
        "allowed": list(allowed),
        "components": components,
        "raw_total": round(sum(d.get("value", 0.0) for d in components.values()), 4),
        "value": round(total, 4),
    }


def _infer_research_score_group(display: str, asset_type: str) -> str:
    """Stage 2.6: Research lab uses universal factors — group inference is deprecated.

    All asset classes now use the same 3 universal factors:
    obv_divergence, bollinger_touch, price_momentum.
    This function returns a generic label for backward-compat diagnostics only.
    """
    return "universal"


def _research_factor_value(
    name: str,
    direction: str,
    candles: list,
    bonus: float,
    penalty: float,
    *,
    cfg: dict | None = None,
    pair: dict | None = None,
    asset_type: str = "",
) -> tuple[float, dict]:
    """Stage 2.6: Universal research lab factors.

    Supported factors:
      - obv_divergence   : OBV trend confirmation
      - bollinger_touch  : Price at band extremes
      - price_momentum   : 10-period rate of change (replaces stochastic_cross + chandelier)
    """
    try:
        if name == "obv_divergence":
            return _research_obv_value(direction, candles, bonus, penalty)
        if name == "bollinger_touch":
            return _research_bollinger_value(direction, candles, bonus, penalty)
        if name == "price_momentum":
            return _research_price_momentum_value(direction, candles, bonus, penalty)
        # Legacy factors — still callable but not in default factor list
        if name == "stochastic_cross":
            return _research_stochastic_value(direction, candles, bonus, penalty, cfg=cfg, pair=pair, asset_type=asset_type)
        if name == "chandelier_trend":
            return _research_chandelier_value(direction, candles, bonus, penalty)
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


def _research_stochastic_value(
    direction: str,
    candles: list,
    bonus: float,
    penalty: float,
    *,
    cfg: dict | None = None,
    pair: dict | None = None,
    asset_type: str = "",
) -> tuple[float, dict]:
    from indicators import calc_stochastic

    stoch_cfg = ((cfg or {}).get("STOCHASTIC_CROSS") or {})
    if stoch_cfg and not stoch_cfg.get("ENABLED", False):
        return 0.0, {"signal": "disabled", "value": 0.0}

    allowed_assets = {str(x).lower() for x in stoch_cfg.get("ASSET_TYPES", [])}
    if allowed_assets and str(asset_type).lower() not in allowed_assets:
        return 0.0, {"signal": "out_of_scope", "reason": "asset_type_not_enabled", "value": 0.0}

    display = str((pair or {}).get("display") or (pair or {}).get("symbol") or "").upper()
    enabled_symbols = {str(x).upper() for x in stoch_cfg.get("SYMBOLS", [])}
    if enabled_symbols and display not in enabled_symbols:
        return 0.0, {
            "signal": "out_of_scope",
            "reason": "symbol_not_enabled",
            "symbol": display,
            "value": 0.0,
        }

    k_periods = stoch_cfg.get("K_PERIODS", [14])
    k_smooth = int(stoch_cfg.get("K_SMOOTH", 3))
    d_smooth = int(stoch_cfg.get("D_SMOOTH", 3))
    timeframe = str(stoch_cfg.get("TIMEFRAME", "H4"))
    paper_tool_only = bool(stoch_cfg.get("PAPER_TOOL_ONLY", False))

    if not candles or len(candles) < max(int(x) for x in k_periods):
        return 0.0, {"signal": "missing", "timeframe": timeframe, "value": 0.0}

    checked: list[dict] = []
    opposing = None
    for raw_period in k_periods:
        kp = int(raw_period)
        stoch = calc_stochastic(candles, kp, k_smooth, d_smooth)
        k = stoch.get("k", [])
        d = stoch.get("d", [])
        if len(k) < 2 or len(d) < 2 or k[-1] is None or d[-1] is None or k[-2] is None or d[-2] is None:
            checked.append({"k_period": kp, "signal": "missing"})
            continue
        crossed_up = k[-1] > d[-1] and k[-2] <= d[-2]
        crossed_down = k[-1] < d[-1] and k[-2] >= d[-2]
        base = {
            "k_period": kp,
            "k": round(k[-1], 2),
            "d": round(d[-1], 2),
        }
        if direction == "LONG" and crossed_up:
            return bonus, {
                **base,
                "signal": "bull_cross",
                "timeframe": timeframe,
                "paper_tool_only": paper_tool_only,
                "value": bonus,
            }
        if direction == "SHORT" and crossed_down:
            return bonus, {
                **base,
                "signal": "bear_cross",
                "timeframe": timeframe,
                "paper_tool_only": paper_tool_only,
                "value": bonus,
            }
        if (direction == "LONG" and crossed_down) or (direction == "SHORT" and crossed_up):
            opposing = {
                **base,
                "signal": "opposing_cross",
                "timeframe": timeframe,
                "paper_tool_only": paper_tool_only,
                "value": penalty,
            }
        checked.append({**base, "signal": "no_cross"})

    if opposing is not None:
        return penalty, opposing
    return 0.0, {
        "signal": "no_cross",
        "timeframe": timeframe,
        "paper_tool_only": paper_tool_only,
        "checked": checked,
        "value": 0.0,
    }


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
    variance = sum((x - mean) ** 2 for x in window) / max(1, len(window) - 1)
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


def _research_price_momentum_value(direction: str, candles: list, bonus: float, penalty: float) -> tuple[float, dict]:
    """10-period rate of change momentum factor.

    Replaces stochastic_cross + chandelier_trend with a single
    universal momentum measure.
    """
    closes = [float(c["close"]) for c in candles if c.get("close") is not None]
    if len(closes) < 15:
        return 0.0, {"signal": "missing", "value": 0.0}
    roc = (closes[-1] - closes[-11]) / abs(closes[-11]) if closes[-11] != 0 else 0.0
    if direction == "LONG" and roc > 0.02:
        return bonus, {"signal": "positive_momentum", "roc": round(roc, 4), "value": bonus}
    if direction == "SHORT" and roc < -0.02:
        return bonus, {"signal": "negative_momentum", "roc": round(roc, 4), "value": bonus}
    if (direction == "LONG" and roc < -0.02) or (direction == "SHORT" and roc > 0.02):
        return penalty, {"signal": "opposing_momentum", "roc": round(roc, 4), "value": penalty}
    return 0.0, {"signal": "neutral_momentum", "roc": round(roc, 4), "value": 0.0}


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

def _adx_gate(
    d1_snap: dict, h4_snap: dict, asset_type: str,
    score_group: str | None = None,
) -> tuple:
    """ADX trend-strength gate with sigmoid scaling.

    Returns (multiplier, adx_value, adx_source).
    multiplier is a continuous linear ramp:
      ADX ≤ hard_fail  → 0.0 (dead market)
      ADX = hard_fail + 25% of range  → 0.25
      ADX = hard_fail + 50% of range  → 0.50
      ADX = hard_fail + 75% of range  → 0.75
      ADX ≥ trend_min  → 1.0 (full credit)

    The old 3-tier step (0.0 / 0.65 / 1.0) made the soft zone too punitive:
    max final_score in soft zone = 3.0 × 0.65 = 1.955, below all asset-class
    thresholds (crypto 2.4, forex 2.1, commodity 1.8, stock 1.8).

    Thresholds are read per-class from config.yaml:
      ADX_TREND_MIN_CLASS[asset_type]  → upper bound (full credit)
      FACTOR_ADX_HARD_FAIL_CLASS[asset_type] → lower bound (zero)

    Backward compatibility: if a class is missing from config, falls back to
    hardcoded hard_fail=10, trend_min=30 and emits a single warnings.warn.

    Source preference: D1 ADX (structural) first, H4 ADX fallback.
    """
    global _adx_fallback_warned

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
        if CONFIG.get("ADX_MISSING_BOTH_ABORT", False):
            return 0.0, None, "missing_both_abort"
        # Soft fallback when ADX missing: use 0.5 (neutral)
        return 0.5, None, "missing"

    # ── Read per-class thresholds from config (score_group → asset_type) ────
    _trend_min_cfg = CONFIG.get("ADX_TREND_MIN_CLASS") or {}
    _hard_fail_cfg = CONFIG.get("FACTOR_ADX_HARD_FAIL_CLASS") or {}

    trend_min = _resolve_class_keyed(_trend_min_cfg, score_group, asset_type, None)
    hard_fail = _resolve_class_keyed(_hard_fail_cfg, score_group, asset_type, None)

    # Backward compatibility: fall back individually per missing key
    _used_fallback = False
    if trend_min is None:
        _used_fallback = True
        trend_min = 30.0
    if hard_fail is None:
        _used_fallback = True
        hard_fail = 10.0

    if _used_fallback and not _adx_fallback_warned:
        _adx_fallback_warned = True
        warnings.warn(
            f"ADX thresholds for asset_type='{asset_type}' not found in config. "
            f"Falling back to hardcoded defaults (hard_fail=10.0, trend_min=30.0) "
            f"for missing entries. Add ADX_TREND_MIN_CLASS and/or "
            f"FACTOR_ADX_HARD_FAIL_CLASS entries to config.yaml to silence.",
            UserWarning,
            stacklevel=2,
        )

    trend_min = float(trend_min)
    hard_fail = float(hard_fail)

    # Sanity: ensure hard_fail < trend_min so the ramp has positive width
    if hard_fail >= trend_min:
        hard_fail = trend_min - 5.0

    # Linear ramp: 0.0 at hard_fail → 1.0 at trend_min
    _range = trend_min - hard_fail
    if _range <= 0:
        mult = 1.0 if adx >= trend_min else 0.0
    else:
        mult = max(0.0, min(1.0, (adx - hard_fail) / _range))
    return mult, adx, source


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
        cot_fade_cfg = CONFIG.get("ENGINE_A_COT_CONTRARIAN_FADE", {}) or {}
        fade_enabled = bool(cot_fade_cfg.get("ENABLED", True))
        fade_assets = set(cot_fade_cfg.get("ASSET_TYPES", ["forex", "commodity"]) or [])
        _fade_lo = float(cot_fade_cfg.get("FADE_START_Z", 1.5))
        _fade_hi = float(cot_fade_cfg.get("FULL_FADE_Z", 2.5))
        if fade_enabled and asset_type in fade_assets and abs(cot) > _fade_lo:
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


def _cot_formula_supported(pair_display: str) -> bool:
    """Return whether this display has an explicit COT/proxy formula."""
    try:
        from cot_feed import _PAIR_FORMULA as _COT_FORMULA
        formula = _COT_FORMULA.get(pair_display)
        return bool(formula)
    except Exception:
        return False


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
        if is_long:
            if oi_chg > 0 and px_chg > 0:
                return _ADDON_CONFIRM    # smart money adding longs
            if oi_chg < 0 and px_chg > 0:
                return _ADDON_NEUTRAL    # short covering only, not smart money
            if oi_chg < 0 and px_chg < 0:
                return _ADDON_AGAINST    # longs capitulating
            if oi_chg > 0 and px_chg < 0:
                return _ADDON_AGAINST    # shorts adding into falling price
            return _ADDON_NEUTRAL
        if oi_chg > 0 and px_chg < 0:
            return _ADDON_CONFIRM    # shorts adding into falling price
        if oi_chg > 0 and px_chg > 0:
            return _ADDON_AGAINST    # smart money adding longs
        if oi_chg < 0 and px_chg > 0:
            return _ADDON_AGAINST    # short covering only — not confirmed trend
        if oi_chg < 0 and px_chg < 0:
            return _ADDON_CONFIRM    # longs capitulating
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
            same_side_combo = (
                funding_val != 0.0
                and oi_val != 0.0
                and ((funding_val > 0 and oi_val > 0) or (funding_val < 0 and oi_val < 0))
            )
            if same_side_combo:
                combo_val = funding_val + oi_val
                combo_hi = float(CONFIG.get("FACTOR_CRYPTO_ADDON_COMBO_CONFIRM_CAP", 0.25))
                combo_lo = float(CONFIG.get("FACTOR_CRYPTO_ADDON_COMBO_AGAINST_CAP", -0.20))
                val = max(combo_lo, min(combo_hi, combo_val))
            else:
                val = max(_ADDON_AGAINST, min(_ADDON_CONFIRM, val))
            status = "ok" if funding_rate is not None else "oi_only"
        else:
            val = funding_val
            status = "ok" if funding_rate is not None else "missing"
        return val, "funding+oi", status

    cot_asset_types = set(CONFIG.get("ENGINE_A_COT_ADDON_ASSET_TYPES", ["commodity"]) or [])
    if asset_type in cot_asset_types:
        addon_type = "cot_proxy" if asset_type in ("stock", "index") else "cot"
        if not _cot_formula_supported(display):
            return _ADDON_NEUTRAL, addon_type, "unsupported"
        val = _cot_addon(display, asset_type, direction, bar_time)
        return val, addon_type, "ok" if val != _ADDON_NEUTRAL else "neutral"

    # stock / index — no addon
    return _ADDON_NEUTRAL, "none", "unsupported"


# ── Factor 4: VWAP Direction Quality Filter (config-gated, multiplicative) ──

def _vwap_direction_filter(
    h4_candles: list,
    direction: str,
    asset_type: str,
) -> tuple[float, dict]:
    """VWAP direction quality filter: small multiplier based on price vs VWAP.

    Returns (multiplier, detail) where multiplier is in [MAX_PENALTY, MAX_BOOST].
    Price above VWAP favours LONGs; price below favours SHORTs.
    Disabled by default — gate via config.

    Uses session-anchored VWAP with configurable lookback period.
    """
    cfg = CONFIG.get("ENGINE_A_VWAP_FILTER", {}) or {}
    if not cfg.get("ENABLED", False):
        return 1.0, {"enabled": False}

    max_boost = float(cfg.get("MAX_BOOST", 0.03))
    max_penalty = float(cfg.get("MAX_PENALTY", -0.03))
    lookback = int(cfg.get("CANDLE_LOOKBACK", 96))

    if not h4_candles or len(h4_candles) < lookback:
        return 1.0, {"enabled": True, "applied": False, "reason": "insufficient_candles"}

    # Use last N candles for VWAP calculation
    vwap_candles = h4_candles[-lookback:]
    current_price = float(h4_candles[-1].get("close", 0))
    if current_price <= 0:
        return 1.0, {"enabled": True, "applied": False, "reason": "invalid_price"}

    try:
        from indicators import calc_vwap
        vwap_result = calc_vwap(vwap_candles, anchor_index=0)
        vwap_values = vwap_result.get("vwap", [])
        if not vwap_values or vwap_values[-1] is None:
            return 1.0, {"enabled": True, "applied": False, "reason": "vwap_calc_failed"}

        vwap = float(vwap_values[-1])
        if vwap <= 0:
            return 1.0, {"enabled": True, "applied": False, "reason": "invalid_vwap"}

        # Calculate distance from VWAP as percentage
        vwap_distance_pct = (current_price - vwap) / vwap

        # Determine multiplier based on direction alignment
        # LONG: price above VWAP = boost, below = penalty
        # SHORT: price below VWAP = boost, above = penalty
        is_long = direction == "LONG"
        if is_long:
            if vwap_distance_pct > 0:
                # Price above VWAP favours LONG
                # Scale boost by distance (capped at MAX_BOOST)
                multiplier = 1.0 + min(max_boost, vwap_distance_pct * 10.0)
            else:
                # Price below VWAP penalises LONG
                multiplier = 1.0 + max(max_penalty, vwap_distance_pct * 10.0)
        else:
            if vwap_distance_pct < 0:
                # Price below VWAP favours SHORT
                multiplier = 1.0 + min(max_boost, abs(vwap_distance_pct) * 10.0)
            else:
                # Price above VWAP penalises SHORT
                multiplier = 1.0 + max(max_penalty, -abs(vwap_distance_pct) * 10.0)

        return multiplier, {
            "enabled": True,
            "applied": True,
            "vwap": round(vwap, 6),
            "current_price": round(current_price, 6),
            "vwap_distance_pct": round(vwap_distance_pct, 6),
            "multiplier": round(multiplier, 4),
        }
    except Exception as exc:
        log.debug("[EA2] VWAP filter error: %s", exc)
        return 1.0, {"enabled": True, "applied": False, "reason": "error"}


# ── Factor 5: Mean Reversion (config-gated, additive) ───────────────────────

def _mean_reversion_factor(
    h4_snap: dict,
    h4_candles: list,
    direction: str,
    asset_type: str,
) -> tuple[float, dict]:
    """Mean reversion adjustment: penalise overextended moves, reward pullbacks.

    Returns (adjustment, detail) where adjustment is a small delta applied
    to the final score (±MAX_ABS).  Disabled by default — gate via config.

    Uses three sub-factors:
      1. Bollinger %B  — price position within bands
      2. RSI extreme   — >80 overbought, <20 oversold
      3. Z-score       — price distance from 20-period mean
    """
    cfg = CONFIG.get("ENGINE_A_MEAN_REVERSION", {}) or {}
    if not cfg.get("ENABLED", False):
        return 0.0, {"enabled": False}

    max_abs = abs(float(cfg.get("MAX_ABS", 0.15)))
    bb_weight = float(cfg.get("BB_WEIGHT", 0.40))
    rsi_weight = float(cfg.get("RSI_WEIGHT", 0.35))
    z_weight = float(cfg.get("Z_WEIGHT", 0.25))

    closes = [float(c["close"]) for c in (h4_candles or []) if c.get("close") is not None]
    if len(closes) < 25:
        return 0.0, {"enabled": True, "applied": False, "reason": "insufficient_candles"}

    # ── Bollinger %B ──
    window = closes[-20:]
    mean = sum(window) / len(window)
    variance = sum((x - mean) ** 2 for x in window) / max(1, len(window) - 1)
    std = math.sqrt(max(0.0, variance))
    upper = mean + 2.0 * std
    lower = mean - 2.0 * std
    band_range = upper - lower if upper != lower else 1e-9
    pct_b = (closes[-1] - lower) / band_range  # 0 = lower band, 1 = upper band

    # %B contribution: overbought → negative (reversion down), oversold → positive (reversion up)
    # But direction matters: if LONG and overbought → fade the long (negative)
    # If SHORT and overbought → confirm the short (positive)
    is_long = direction == "LONG"
    bb_raw = (pct_b - 0.5) * 2.0  # centre at 0.5 → range [-1, +1]
    bb_score = -bb_raw if is_long else bb_raw  # invert: overbought hurts LONGs, helps SHORTs

    # ── RSI extreme ──
    rsi = h4_snap.get("rsi")
    rsi_score = 0.0
    if rsi is not None:
        try:
            rsi = float(rsi)
            # Extreme RSI → fade the move
            if rsi > 80:
                rsi_score = -1.0 if is_long else 1.0
            elif rsi < 20:
                rsi_score = 1.0 if is_long else -1.0
            elif rsi > 70:
                rsi_score = -0.5 if is_long else 0.5
            elif rsi < 30:
                rsi_score = 0.5 if is_long else -0.5
        except (TypeError, ValueError):
            pass

    # ── Z-score ──
    z = (closes[-1] - mean) / std if std > 0 else 0.0
    z_score = 0.0
    if abs(z) > 2.5:
        z_score = -1.0 if is_long else 1.0
    elif abs(z) > 1.5:
        z_score = -0.5 if is_long else 0.5

    # ── Weighted blend ──
    total_w = bb_weight + rsi_weight + z_weight
    if total_w <= 0:
        total_w = 1.0
    raw = (bb_score * bb_weight + rsi_score * rsi_weight + z_score * z_weight) / total_w
    # Scale to max_abs
    adjustment = max(-max_abs, min(max_abs, raw * max_abs))

    return adjustment, {
        "enabled": True,
        "applied": True,
        "adjustment": round(adjustment, 4),
        "pct_b": round(pct_b, 4),
        "bb_score": round(bb_score, 4),
        "rsi": round(rsi, 2) if rsi is not None else None,
        "rsi_score": round(rsi_score, 4),
        "z_score": round(z, 4),
        "z_raw": round(z_score, 4),
        "max_abs": max_abs,
    }

def _volatility_scaler(
    atr: float,
    close: float,
    asset_type: str,
    score_group: str | None = None,
) -> float:
    """Volatility-based position-quality scaler with per-class and score_group bands.

    Maps ATR/close ratio to a multiplier:
      ATR% ≤ low_band  → mult_low  (reward precision in low-vol)
      ATR% ≥ high_band → mult_high (penalise noise in high-vol)
      between          → linear interpolation

    Bands use ``VOLATILITY_SCALER_BANDS`` with ``_resolve_class_keyed`` so
    ``precious_trackers`` / ``energy_oil`` can override ``stock`` identity.
    """
    if close is None or close <= 0 or atr is None or atr <= 0:
        return 1.0
    atr_pct = atr / close

    _bands = CONFIG.get("VOLATILITY_SCALER_BANDS") or {}
    _class_band = _resolve_class_keyed(_bands, score_group, asset_type, None)
    if isinstance(_class_band, dict) and "low" in _class_band and "high" in _class_band:
        _low = float(_class_band["low"])
        _high = float(_class_band["high"])
    else:
        _low = float(CONFIG.get("VOLATILITY_SCALER_ATR_PCT_LOW", _VOLATILITY_SCALER_ATR_PCT_LOW))
        _high = float(CONFIG.get("VOLATILITY_SCALER_ATR_PCT_HIGH", _VOLATILITY_SCALER_ATR_PCT_HIGH))

    _mult_low = float(CONFIG.get("VOLATILITY_SCALER_MULT_LOW", _VOLATILITY_SCALER_MULT_LOW))
    _mult_high = float(CONFIG.get("VOLATILITY_SCALER_MULT_HIGH", _VOLATILITY_SCALER_MULT_HIGH))
    if atr_pct <= _low:
        return _mult_low
    if atr_pct >= _high:
        return _mult_high
    t = (atr_pct - _low) / (_high - _low)
    return _mult_low + t * (_mult_high - _mult_low)


def _session_multiplier(bar_time: Optional[str], asset_type: str) -> float:
    """Forex session liquidity multiplier — off unless ``FACTOR_FOREX_SESSION_MULT.ENABLED``.

    Uses UTC hour buckets from ``scoring.get_session`` (same labels as scan UI).
    """
    if str(asset_type or "").lower() != "forex":
        return 1.0
    cfg = CONFIG.get("FACTOR_FOREX_SESSION_MULT") or {}
    if not bool(cfg.get("ENABLED", False)):
        return 1.0
    try:
        from scoring import get_session

        qual = str((get_session(bar_time) or {}).get("quality") or "medium")
    except Exception:
        qual = "medium"
    mults = cfg.get("BY_QUALITY") or {}
    default_map = {"high": 1.0, "medium": 0.95, "low": 0.85}
    if not isinstance(mults, dict) or not mults:
        mults = default_map
    try:
        return float(mults.get(qual, default_map.get(qual, 1.0)))
    except (TypeError, ValueError):
        return 1.0


def _utc_decimal_hour(bar_time: Optional[str]) -> float | None:
    if not bar_time:
        return None
    try:
        if isinstance(bar_time, datetime):
            dt = bar_time
        else:
            text = str(bar_time).strip()
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            dt = datetime.fromisoformat(text)
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc)
        return float(dt.hour) + float(dt.minute) / 60.0
    except Exception:
        return None


def _equity_session_multiplier(
    bar_time: Optional[str],
    asset_type: str,
) -> tuple[float, dict]:
    """Optional US-session liquidity weighting for stock/index/ETF Engine A scores."""
    cfg = CONFIG.get("ENGINE_A_EQUITY_SESSION_LIQUIDITY_WEIGHTING") or {}
    detail = {"enabled": bool(cfg.get("ENABLED", False)), "applied": False, "multiplier": 1.0}
    if not detail["enabled"]:
        return 1.0, detail
    asset_l = str(asset_type or "").lower()
    assets = set(cfg.get("ASSET_TYPES") or ["stock", "index", "etf", "etf_bond"])
    if asset_l not in assets:
        detail["reason"] = "asset_not_enabled"
        return 1.0, detail

    hour = _utc_decimal_hour(bar_time)
    if hour is None:
        detail["reason"] = "missing_or_invalid_bar_time"
        return 1.0, detail

    start = _float_cfg(cfg.get("ACTIVE_UTC_START_HOUR"), 13.5)
    end = _float_cfg(cfg.get("ACTIVE_UTC_END_HOUR"), 20.0)
    if start <= end:
        active = start <= hour < end
    else:
        active = hour >= start or hour < end
    mult_key = "ACTIVE_MULT" if active else "OFF_HOURS_MULT"
    mult = _float_cfg(cfg.get(mult_key), 1.0)
    detail.update({
        "applied": True,
        "session": "us_active" if active else "off_hours",
        "utc_hour": round(hour, 4),
        "multiplier": round(mult, 6),
    })
    return mult, detail


def _volatility_regime_multiplier(
    regime: str,
    asset_type: str,
    score_group: str | None,
) -> tuple[float, dict]:
    """Optional score multiplier for asset classes where regime changes quality."""
    enabled = bool(CONFIG.get("ENGINE_A_VOLATILITY_REGIME_ADJUSTMENT_ENABLED", False))
    detail = {
        "enabled": enabled,
        "applied": False,
        "regime": regime,
        "multiplier": 1.0,
    }
    if not enabled:
        return 1.0, detail

    keyed = CONFIG.get("ENGINE_A_VOLATILITY_REGIME_MULTIPLIERS") or {}
    resolved = _resolve_class_keyed(keyed, score_group, asset_type, {})
    mult = 1.0
    if isinstance(resolved, dict):
        mult = _float_cfg(resolved.get(regime, resolved.get("default")), 1.0)
    elif resolved is not None:
        mult = _float_cfg(resolved, 1.0)

    bounds = CONFIG.get("ENGINE_A_VOLATILITY_REGIME_MULT_BOUNDS") or {}
    min_mult = _float_cfg(bounds.get("min"), 0.85)
    max_mult = _float_cfg(bounds.get("max"), 1.15)
    if max_mult < min_mult:
        min_mult, max_mult = max_mult, min_mult
    mult = max(min_mult, min(max_mult, mult))
    detail.update({"applied": abs(mult - 1.0) > 1e-9, "multiplier": round(mult, 6)})
    return mult, detail


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
    volume_threshold: Optional[float] = None,
    structure_result: Optional[dict] = None,
) -> Dict:
    """Compute Engine A v2 factor scores and aggregate to final conviction score.

    Returns dict with final_score (0-3.0), direction, factor_scores, regime, and diagnostics.
    Backward-compatible keys preserved for Engine C / scoring.py / Marcus Reid AI.
    """
    asset_type = pair.get("type", "stock")
    display = pair.get("display", pair.get("symbol", "?"))
    pair_id = display
    feed_status: Dict[str, str] = {}

    # Resolve score_group once so per-subgroup config (e.g. precious_trackers
    # RSI bounds for GLD-as-stock) can override asset_type lookups everywhere
    # Engine A reads class-keyed config.
    score_group = _resolve_pair_score_group(pair)

    # ── Data quality guard ────────────────────────────────────────────────────
    _close = h4_snap.get("close")
    _atr = h4_snap.get("atr")
    if _close and _atr == 0:
        log.warning("[EA2] %s ATR=0 — frozen candle data suspected", display)
        regime_raw = detect_regime(h4_snap, asset_type).get("regime", "UNKNOWN")
        regime = _get_smoothed_regime(regime_context, pair_id, regime_raw)
        return _zero_result(pair, regime, {"error": "atr_zero"}, feed_status,
                            reason="atr_zero_abort", direction=None)

    # ── FACTOR 1: Trend ───────────────────────────────────────────────────────
    d1_prev = _previous_indicator_snap(d1_candles)
    h4_prev = _previous_indicator_snap(h4_candles)
    h1_prev = _previous_indicator_snap(h1_candles)
    trend_score, direction, trend_detail = _coherent_trend_score(
        d1_snap, h4_snap, h1_snap, asset_type,
        d1_prev=d1_prev,
        h4_prev=h4_prev,
        h1_prev=h1_prev,
        score_group=score_group,
    )
    trend_detail["hysteresis_prev_available"] = {
        "d1": d1_prev is not None,
        "h4": h4_prev is not None,
        "h1": h1_prev is not None,
    }

    # Hard abort: no direction determinable
    if direction is None or abs(trend_score) < 1e-9:
        log.debug("[EA2] %s trend indeterminate — score=0", display)
        regime_raw = detect_regime(h4_snap, asset_type).get("regime", "UNKNOWN")
        regime = _get_smoothed_regime(regime_context, pair_id, regime_raw)
        return _zero_result(pair, regime, trend_detail, feed_status, reason="indeterminate_trend")

    # ── Directional gate: hard cut + soft confidence ramp ────────────────────
    # Below `min_directional`: abort (trend signal is too weak to justify any score).
    # Inside the soft-span window: linear ramp 0→1 multiplied into base_score so a
    # weak-but-present trend produces a proportionally smaller score, instead of
    # the binary cliff at the threshold.
    _min_directional, _soft_span = _resolve_directional_ramp(asset_type, score_group)
    _abs_trend = abs(trend_score)
    if _abs_trend < _min_directional:
        log.debug(
            "[EA2] %s abs(trend)=%.3f < min_directional=%.3f — abort",
            display, _abs_trend, _min_directional,
        )
        regime_raw = detect_regime(h4_snap, asset_type).get("regime", "UNKNOWN")
        regime = _get_smoothed_regime(regime_context, pair_id, regime_raw)
        return _zero_result(
            pair, regime, trend_detail, feed_status,
            reason="min_directional_failed", direction=direction,
            min_directional=_min_directional,
            min_directional_failed=True,
        )
    if _soft_span > 0 and _abs_trend < _min_directional + _soft_span:
        dir_ramp_mult = (_abs_trend - _min_directional) / _soft_span
    else:
        dir_ramp_mult = 1.0

    # ── FACTOR 3: ADX gate ────────────────────────────────────────────────────
    adx_mult, adx_val, adx_source = _adx_gate(d1_snap, h4_snap, asset_type, score_group=score_group)
    feed_status["adx"] = adx_source

    # Hard abort: dead market (ADX ≤ 10)
    if adx_mult == 0.0:
        log.debug("[EA2] %s ADX=%.1f hard abort — dead market", display, adx_val or 0)
        regime_raw = detect_regime(h4_snap, asset_type).get("regime", "UNKNOWN")
        regime = _get_smoothed_regime(regime_context, pair_id, regime_raw)
        return _zero_result(pair, regime, trend_detail, feed_status, reason="adx_hard_abort",
                            adx_val=adx_val, direction=direction)

    # ── FACTOR 2: Momentum quality ────────────────────────────────────────────
    mom_quality = _momentum_quality(h4_snap, direction, asset_type, score_group=score_group)

    # Apply Stochastic RSI modifier (experimental, config-gated)
    stoch_rsi_adj = _stochastic_rsi_modifier(h4_candles, direction, asset_type)
    mom_quality = max(0.0, min(1.0, mom_quality + stoch_rsi_adj))
    if stoch_rsi_adj != 0.0:
        feed_status["stoch_rsi"] = f"{stoch_rsi_adj:.2f}"

    # ── ADDON: Asset-specific secondary factor ────────────────────────────────
    addon_val, addon_type, addon_status = _asset_addon(
        pair, direction, funding_rate, bar_time, oi_context=oi_context
    )
    _asset_addon_exceeds_single_cap = (
        asset_type == "crypto"
        and (addon_val > _ADDON_CONFIRM or addon_val < _ADDON_AGAINST)
    )
    feed_status["addon"] = f"{addon_type}:{addon_status}"
    if asset_type == "stock":
        if bool(CONFIG.get("INSIDER_TRADING_ENABLED", False)):
            feed_status["insider_trading"] = "advisory_only"
        if bool(CONFIG.get("FUNDAMENTALS_ENABLED", False)):
            feed_status["fundamentals"] = "advisory_only"

    research_val, research_detail = _research_lab_candidate_addon(
        pair, direction, h4_candles, d1_candles, asset_type, score_group
    )
    if research_detail.get("enabled"):
        addon_val += research_val
        feed_status["research_lab"] = f"{research_val:.2f}"

    # Stage 3.6: Cross-engine research lab correlation cap.
    # If Engine B research lab is also active for this pair, the two labs may
    # double-count the same price-action evidence. Cap total research contribution
    # so it cannot exceed the standalone research bonus by more than 50%.
    _engine_b_rl_enabled = bool(
        (CONFIG.get("ENGINE_B_RESEARCH_LAB_FACTORS") or {}).get("ENABLED", False)
    )
    if _engine_b_rl_enabled and research_detail.get("enabled"):
        _standalone_max = float(CONFIG.get("ENGINE_A_RESEARCH_MAX", _RESEARCH_BONUS_DEFAULT))
        _cross_engine_max = _standalone_max * 1.5
        _total_research = addon_val - (_ADDON_NEUTRAL if addon_status == "unsupported" else 0.0)
        # Only cap the research portion, not the carry/funding/oi portion
        _base_addon = addon_val - research_val
        _capped_research = max(-_cross_engine_max, min(_cross_engine_max, research_val))
        addon_val = _base_addon + _capped_research
        feed_status["research_lab_capped"] = f"{_capped_research:.2f}"

    # Stage 1.4: unified addon bound aligned with research lab MAX_ABS.
    # Crypto funding+OI can opt into a wider cap only when the asset addon
    # itself exceeded the single-signal band before research extras were added.
    _addon_hi = _ADDON_CONFIRM
    _addon_lo = _ADDON_AGAINST
    if _asset_addon_exceeds_single_cap:
        _addon_hi = max(
            _ADDON_CONFIRM,
            float(CONFIG.get("FACTOR_CRYPTO_ADDON_COMBO_CONFIRM_CAP", 0.25)),
        )
        _addon_lo = min(
            _ADDON_AGAINST,
            float(CONFIG.get("FACTOR_CRYPTO_ADDON_COMBO_AGAINST_CAP", -0.20)),
        )
    addon_val = max(_addon_lo, min(_addon_hi, addon_val))

    # ── FACTOR 4: VWAP Direction Quality Filter (config-gated) ───────────────
    vwap_mult, vwap_detail = _vwap_direction_filter(
        h4_candles, direction, asset_type
    )
    if vwap_detail.get("enabled"):
        feed_status["vwap_filter"] = f"{vwap_mult:.2f}"

    # ── FACTOR 5: Mean Reversion (config-gated) ─────────────────────────────
    mean_rev_adj, mean_rev_detail = _mean_reversion_factor(
        h4_snap, h4_candles, direction, asset_type
    )
    if mean_rev_detail.get("enabled"):
        feed_status["mean_reversion"] = f"{mean_rev_adj:.2f}"

    # ── Volatility scaler (Stage 3.4) ─────────────────────────────────────────
    _close_for_vol = h4_snap.get("close")
    try:
        _close_for_vol = float(_close_for_vol) if _close_for_vol is not None else None
    except (TypeError, ValueError):
        _close_for_vol = None
    vol_scaler = _volatility_scaler(
        _atr if _atr else 0.0, _close_for_vol, asset_type, score_group
    )

    # nat_gas and crypto_doge are structurally-volatile subgroups whose H4 ATR%
    # routinely sits in the parent class's "noise" band even during normal trade.
    # Their group multipliers already encode "high vol = opportunity", so the
    # quality scaler is clamped to neutral-or-boost only.  The crypto-class
    # clamp was removed once VOLATILITY_SCALER_BANDS["crypto"] was widened to
    # match real BTC/ETH ATR%.
    if score_group in ("nat_gas", "crypto_doge"):
        vol_scaler = max(1.0, vol_scaler)

    feed_status["vol_scaler"] = f"{vol_scaler:.2f}"

    # ── Session multiplier (deprecated, now returns 1.0) ──────────────────────
    session_mult = _session_multiplier(bar_time, asset_type)
    feed_status["session"] = f"{session_mult:.2f}"

    # Optional equity-session weighting is isolated from the existing forex
    # session multiplier and is default-off to preserve current Engine A scores.
    equity_session_mult, equity_session_detail = _equity_session_multiplier(bar_time, asset_type)
    if equity_session_detail.get("enabled"):
        feed_status["equity_session"] = str(equity_session_detail.get("session") or equity_session_detail.get("reason") or "neutral")

    # ── +DI/-DI directional alignment multiplier ──────────────────────────────
    # Stage 1.3: ADX measures strength but not direction. If EMA says LONG but
    # -DI > +DI, bearish pressure dominates — score must be suppressed.
    def _di_alignment_multiplier(trend_dir: str, plus_di: float | None, minus_di: float | None) -> float:
        if plus_di is None or minus_di is None:
            return 0.5  # data missing → neutral
        di_diff = plus_di - minus_di
        if trend_dir == "LONG":
            if plus_di > minus_di:
                return 1.0
            elif abs(di_diff) < 5.0:
                return 0.5
            else:
                return 0.0
        elif trend_dir == "SHORT":
            if minus_di > plus_di:
                return 1.0
            elif abs(di_diff) < 5.0:
                return 0.5
            else:
                return 0.0
        return 0.5

    _plus_di = h4_snap.get("plusDI") or h4_snap.get("plus_di")
    _minus_di = h4_snap.get("minusDI") or h4_snap.get("minus_di")
    di_align_mult = _di_alignment_multiplier(direction, _plus_di, _minus_di)
    feed_status["di_align"] = f"{di_align_mult:.2f}"
    if di_align_mult == 0.0:
        feed_status["abort_reason"] = "DI_ALIGNMENT_CONFLICT"
        log.debug(
            "[EA2] %s DI alignment conflict direction=%s plusDI=%s minusDI=%s",
            display,
            direction,
            _plus_di,
            _minus_di,
        )

    # ── Conviction score: weighted combination ────────────────────────────────
    # Read weights lazily so config reloads take effect without restart.
    factor_weight_cfg = _resolve_factor_weights(score_group, asset_type)
    _momentum_w = factor_weight_cfg["momentum"]
    _addon_w = factor_weight_cfg["addon"]
    _base_w = factor_weight_cfg["base"]
    _conviction_floor = _float_cfg(CONFIG.get("FACTOR_CONVICTION_FLOOR"), _CONVICTION_FLOOR_DEFAULT)
    # conviction ∈ [0, 1] — how strongly we believe in this setup
    # Base floor + momentum quality + addon (addon_val can be negative → reduces conviction).
    # Normalise addon: +0.20 → +1.0, 0.00 → 0.0, -0.15 → -0.75 (penalty preserved, not floored).
    addon_norm = (addon_val / _ADDON_CONFIRM) if _ADDON_CONFIRM > 0 else 0.0

    # When addon is unsupported (stock/index), redistribute addon weight.
    # ADDON_UNSUPPORTED_SPLIT (default 0.5): fraction of addon weight that goes
    # to base (raises floor) vs momentum (amplifies single-factor).  At 0.5 the
    # split is 50/50 so momentum carries 0.65 instead of 0.80.
    _eff_mom_w = _momentum_w
    _eff_addon_w = _addon_w
    _eff_base_w = _base_w
    if addon_status == "unsupported":
        _split_to_base = _resolve_addon_split(score_group, asset_type)
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
    # applied: adx_mult * session_mult * di_align_mult * conviction blend
    # Formula: abs(trend_score) * adx_mult * session_mult * di_align_mult *
    #          (floor + (1-floor)*conviction)
    #
    # The conviction floor is regime-conditional when CONVICTION_FLOOR_BY_REGIME is
    # present. RANGING / HIGH_VOLATILITY use a lower floor (default 0.40) because
    # momentum noise is higher — weak momentum should count for less.
    _floor_by_regime = CONFIG.get("CONVICTION_FLOOR_BY_REGIME") or {}
    if isinstance(_floor_by_regime, dict) and regime in _floor_by_regime:
        _eff_floor = float(_floor_by_regime[regime])
    else:
        _eff_floor = _conviction_floor
    _eff_floor = _resolve_conviction_floor(score_group, asset_type, _eff_floor)

    vol_regime_mult, vol_regime_detail = _volatility_regime_multiplier(
        regime, asset_type, score_group
    )
    if vol_regime_detail.get("enabled"):
        feed_status["vol_regime"] = f"{vol_regime_detail.get('multiplier', 1.0):.2f}"

    base_score = (
        abs(trend_score)
        * adx_mult
        * vol_scaler
        * session_mult
        * equity_session_mult
        * vol_regime_mult
        * di_align_mult
        * dir_ramp_mult
        * vwap_mult
    )

    # FIX 3: Recalibrate Cost/Funding Penalty Sensitivity
    _cost_penalty = 0.0
    _fund_high = float(CONFIG.get("FACTOR_FUNDING_HIGH_PCT", 0.01))
    _fund_low = float(CONFIG.get("FACTOR_FUNDING_LOW_PCT", -0.01))
    _fund_pen_cap = float(CONFIG.get("FACTOR_FUNDING_PENALTY_CAP", 0.10))
    _fund_pen_mult = float(CONFIG.get("FACTOR_FUNDING_PENALTY_MULT", 5.0))
    _fund_bonus_cap = float(CONFIG.get("FACTOR_FUNDING_BONUS_CAP", 0.05))
    _fund_bonus_mult = float(CONFIG.get("FACTOR_FUNDING_BONUS_MULT", 2.5))
    _carry_neg = float(CONFIG.get("FACTOR_CARRY_NEGATIVE_PCT", -0.02))
    _carry_pos = float(CONFIG.get("FACTOR_CARRY_POSITIVE_PCT", 0.02))
    _carry_pen = float(CONFIG.get("FACTOR_CARRY_PENALTY", 0.05))
    _carry_boost = float(CONFIG.get("FACTOR_CARRY_BOOST", -0.03))
    if asset_type == "crypto" and funding_rate is not None:
        try:
            _fr = float(funding_rate)
            if _fr > _fund_high:  # Expensive to hold
                _cost_penalty = min(_fund_pen_cap, _fr * _fund_pen_mult)
            elif _fr < _fund_low:  # Getting paid to hold
                _cost_bonus = min(_fund_bonus_cap, abs(_fr) * _fund_bonus_mult)
                _cost_penalty = -_cost_bonus  # Negative penalty = boost
            else:
                _cost_penalty = 0  # Normal funding = no penalty
        except (TypeError, ValueError):
            pass
    elif asset_type == "forex":
        # Use raw carry differential for cost penalty (independent of addon_val)
        try:
            from carry_feed import get_carry_differential
            carry = get_carry_differential(display)
            if carry is not None:
                if carry < _carry_neg:  # Negative carry
                    _cost_penalty = _carry_pen
                elif carry > _carry_pos:  # Positive carry
                    _cost_penalty = _carry_boost  # Small boost
                else:
                    _cost_penalty = 0
        except Exception:
            pass

    # ── Phase 2 parameter wiring: volume_ratio, macro_context, intermarket_context ──
    # These parameters were previously accepted but silently ignored.  They now
    # contribute small bounded adjustments (±5% of total score max) to avoid
    # overfitting while making the inputs materially affect the output.

    # Volume-ratio adjustment: elevated volume confirms conviction; low volume
    # suppresses it.  Impact bounded to CONFIG-tunable limits.
    _vol_adj_max = float(CONFIG.get("FACTOR_VOL_ADJ_MAX", 0.03))
    _vol_adj_min = float(CONFIG.get("FACTOR_VOL_ADJ_MIN", -0.03))
    _vol_hi_thresh = float(
        volume_threshold
        if volume_threshold is not None
        else CONFIG.get("FACTOR_VOL_HIGH_THRESHOLD", 1.5)
    )
    _vol_lo_thresh = float(CONFIG.get("FACTOR_VOL_LOW_THRESHOLD", 0.5))
    _vol_adj = 0.0
    if isinstance(volume_ratio, (int, float)) and volume_ratio > 0:
        if volume_ratio > _vol_hi_thresh:
            _vol_adj = min(_vol_adj_max, (volume_ratio - _vol_hi_thresh) * _vol_adj_max)
        elif volume_ratio < _vol_lo_thresh:
            _vol_adj = max(_vol_adj_min, (volume_ratio - _vol_lo_thresh) * abs(_vol_adj_min) * 2)
        # neutral zone → no adjustment

    # Macro-context adjustment: risk-on boosts trend-aligned scores, risk-off
    # dampens them.  Impact bounded to CONFIG-tunable limits.
    _macro_adj_max = float(CONFIG.get("FACTOR_MACRO_ADJ_MAX", 0.02))
    _macro_adj_min = float(CONFIG.get("FACTOR_MACRO_ADJ_MIN", -0.02))
    _macro_adj = 0.0
    if isinstance(macro_context, dict):
        _macro_state = str(macro_context.get("state", "neutral")).lower()
        if _macro_state == "risk_on":
            _macro_adj = _macro_adj_max
        elif _macro_state == "risk_off":
            _macro_adj = _macro_adj_min
    elif isinstance(macro_context, str):
        _macro_s = macro_context.lower()
        if _macro_s == "risk_on":
            _macro_adj = _macro_adj_max
        elif _macro_s == "risk_off":
            _macro_adj = _macro_adj_min

    # Intermarket-context adjustment: legacy divergence payloads still get the
    # small percentage adjustment below. Rich intermarket contexts are applied
    # through intermarket.apply_confirmation_to_score() after the base score.
    _inter_adj_max = float(CONFIG.get("FACTOR_INTER_ADJ_MAX", 0.02))
    _inter_adj_min = float(CONFIG.get("FACTOR_INTER_ADJ_MIN", -0.02))
    _inter_adj = 0.0
    _has_rich_intermarket_context = isinstance(intermarket_context, dict) and any(
        key in intermarket_context
        for key in ("confirmation", "matrix", "relationships", "engineAContext", "enabled")
    )
    if isinstance(intermarket_context, dict) and not _has_rich_intermarket_context:
        if intermarket_context.get("divergence") is True:
            _inter_adj = _inter_adj_min
        _divergence_score = intermarket_context.get("divergence_score")
        if isinstance(_divergence_score, (int, float)):
            _inter_adj = max(_inter_adj_min, min(_inter_adj_max, -_divergence_score * abs(_inter_adj_min)))
    # Total bounded adjustment: clamp sum to ±5% default
    _total_adj_cap = float(CONFIG.get("FACTOR_TOTAL_ADJ_CAP", 0.05))
    _total_adj = max(-_total_adj_cap, min(_total_adj_cap, _vol_adj + _macro_adj + _inter_adj))

    final_score = base_score * (_eff_floor + (1.0 - _eff_floor) * conviction)
    final_score = final_score * (1.0 - _cost_penalty)
    final_score = final_score * (1.0 + _total_adj)
    # Apply mean reversion adjustment (additive, bounded)
    final_score = final_score + mean_rev_adj
    final_score = max(0.0, min(3.0, final_score))

    intermarket_confirmation = None
    intermarket_engine_a_delta = 0.0
    if isinstance(intermarket_context, dict):
        try:
            from intermarket import apply_confirmation_to_score

            _im_result = apply_confirmation_to_score(
                final_score,
                direction,
                pair,
                intermarket_context,
                max_score=3.0,
                config=CONFIG,
            )
            _adjusted_score = _im_result.get("adjusted_score")
            if isinstance(_adjusted_score, (int, float)):
                final_score = max(0.0, min(3.0, float(_adjusted_score)))
            _confirmation = _im_result.get("confirmation")
            if isinstance(_confirmation, dict):
                intermarket_confirmation = _confirmation
                intermarket_engine_a_delta = float(_confirmation.get("engineADelta", 0.0) or 0.0)
                feed_status["intermarket"] = str(_confirmation.get("verdict", "neutral"))
        except Exception as exc:
            log.debug("[EA2] %s intermarket confirmation skipped: %s", display, exc)
            feed_status["intermarket"] = "error"

    structure_adjustment = {
        "enabled": bool(CONFIG.get("ENGINE_A_STRUCTURE_CONTEXT_ENABLED", False)),
        "applied": False,
        "adjusted_score": round(final_score, 6),
    }
    if structure_adjustment["enabled"] and isinstance(structure_result, dict):
        try:
            from athena_app.services.structure_context import apply_structure_context_to_score

            structure_adjustment = apply_structure_context_to_score(
                structure_result,
                direction=direction,
                base_score=float(final_score or 0.0),
                max_score=3.0,
            )
            adjusted = structure_adjustment.get("adjusted_score")
            if isinstance(adjusted, (int, float)):
                final_score = max(0.0, min(3.0, float(adjusted)))
            feed_status["structure_context"] = "applied" if structure_adjustment.get("applied") else "neutral"
        except Exception as exc:
            log.debug("[EA2] %s structure context skipped: %s", display, exc)
            feed_status["structure_context"] = "error"
            structure_adjustment = {
                "enabled": True,
                "applied": False,
                "adjusted_score": round(final_score, 6),
                "error": "structure_context_error",
            }

    asset_diagnostics = {
        "asset_type": asset_type,
        "score_group": score_group,
        "score_group_adjustments_enabled": _engine_a_group_adjustments_enabled(),
        "factor_weights": {
            "configured": {
                "momentum": round(_momentum_w, 6),
                "addon": round(_addon_w, 6),
                "base": round(_base_w, 6),
            },
            "effective": {
                "momentum": round(_eff_mom_w, 6),
                "addon": round(_eff_addon_w, 6),
                "base": round(_eff_base_w, 6),
            },
        },
        "directional_ramp": {
            "min_directional": round(_min_directional, 6),
            "soft_span": round(_soft_span, 6),
            "multiplier": round(dir_ramp_mult, 6),
        },
        "volatility": {
            "atr_pct_scaler": round(vol_scaler, 6),
            "regime_multiplier": vol_regime_detail,
        },
        "equity_session": equity_session_detail,
        "conviction_floor": round(_eff_floor, 6),
    }

    # ── Factor scores dict (for UI / Marcus Reid diagnostics) ─────────────────
    factor_scores = {
        "trend": round(trend_score, 4),
        "momentum": round(mom_quality, 4),
        "addon": round(addon_val, 4),
        "research_lab": round(research_val, 4),
        "mean_reversion": round(mean_rev_adj, 4),
    }

    log.debug(
        "[EA2] %s dir=%s score=%.3f trend=%.3f adx=%.1f(%s) mom=%.3f addon=%.3f(%s) mean_rev=%.3f sess=%.2f regime=%s",
        display, direction, final_score, trend_score, adx_val or 0, adx_source,
        mom_quality, addon_val, addon_type, mean_rev_adj, session_mult, regime,
    )

    return {
        # ── Core outputs ──────────────────────────────────────────────────────
        "final_score": round(final_score, 4),
        "direction": direction,
        "regime": regime,
        # ── Factor breakdown (UI + AI) ────────────────────────────────────────
        "factor_scores": factor_scores,
        "weights": {"trend": 1.0, "momentum": _eff_mom_w, "addon": _eff_addon_w, "base": _eff_base_w},
        "asset_type": asset_type,
        "score_group": score_group,
        "engine_a_asset_diagnostics": asset_diagnostics,
        "structure_context_adjustment": structure_adjustment,
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
        "mean_reversion_value": round(mean_rev_adj, 4),
        "mean_reversion_detail": mean_rev_detail,
        "addon_unsupported": addon_status == "unsupported",
        "session_multiplier": round(session_mult, 4),
        "equity_session_multiplier": round(equity_session_mult, 4),
        "volatility_regime_multiplier": round(vol_regime_mult, 4),
        "conviction": round(conviction, 4),
        # ── Compatibility flags ───────────────────────────────────────────────
        "insufficient_factors": False,
        "indeterminate_direction": False,
        "min_directional_failed": False,
        "active_directional_factors": ["trend"],
        "active_nondirectional_factors": ["momentum", "addon"] + (["research_lab"] if research_detail.get("enabled") else []) + (["mean_reversion"] if mean_rev_detail.get("enabled") else []),
        "disabled_factors": [],
        "directional_confidence_multiplier": round(conviction, 4),
        "directional_ramp_multiplier": round(dir_ramp_mult, 4),
        "effective_min_directional": round(_min_directional + _soft_span, 4),
        "min_directional_threshold": round(_min_directional, 4),
        "optional_factor_coverage": 1.0,
        "missing_directional_optional_count": 0,
        "correlation_adjustments": {},
        "crypto_engine_a_diagnostics": None,
        "intermarket_confirmation": intermarket_confirmation,
        "intermarket_engine_a_delta": round(intermarket_engine_a_delta, 6),
        "feed_status": feed_status,
        "btc_bias_applied": None,
        "filtered_indicators": _confidence_filtered_indicators(
            d1_snap,
            h4_snap,
            h1_snap,
            d1_prev,
            h4_prev,
            h1_prev,
            direction,
            volume_ratio,
            funding_rate,
            mean_rev_detail,
        ),
    }


def _zero_result(
    pair: dict,
    regime: str,
    trend_detail: dict,
    feed_status: dict,
    reason: str = "unknown",
    adx_val=None,
    direction=None,
    min_directional: float = 0.0,
    min_directional_failed: bool = False,
) -> dict:
    """Return a clean zero-score result with diagnostics."""
    asset_type = pair.get("type", "stock")
    score_group = _resolve_pair_score_group(pair)
    weight_cfg = _resolve_factor_weights(score_group, asset_type)
    asset_diagnostics = {
        "asset_type": asset_type,
        "score_group": score_group,
        "score_group_adjustments_enabled": _engine_a_group_adjustments_enabled(),
        "factor_weights": {"configured": weight_cfg, "effective": weight_cfg},
        "directional_ramp": {
            "min_directional": round(min_directional, 6),
            "soft_span": 0.0,
            "multiplier": 0.0,
        },
        "volatility": {
            "atr_pct_scaler": 1.0,
            "regime_multiplier": {
                "enabled": bool(CONFIG.get("ENGINE_A_VOLATILITY_REGIME_ADJUSTMENT_ENABLED", False)),
                "applied": False,
                "regime": regime,
                "multiplier": 1.0,
            },
        },
        "equity_session": {
            "enabled": bool((CONFIG.get("ENGINE_A_EQUITY_SESSION_LIQUIDITY_WEIGHTING") or {}).get("ENABLED", False)),
            "applied": False,
            "multiplier": 1.0,
        },
        "conviction_floor": 0.0,
    }
    return {
        "final_score": 0.0,
        "direction": direction,
        "regime": regime,
        "factor_scores": {"trend": 0.0, "momentum": 0.0, "addon": 0.0, "research_lab": 0.0, "mean_reversion": 0.0},
        "weights": {"trend": 1.0, **weight_cfg},
        "asset_type": asset_type,
        "score_group": score_group,
        "engine_a_asset_diagnostics": asset_diagnostics,
        "structure_context_adjustment": {
            "enabled": bool(CONFIG.get("ENGINE_A_STRUCTURE_CONTEXT_ENABLED", False)),
            "applied": False,
            "adjusted_score": 0.0,
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
        "mean_reversion_value": 0.0,
        "mean_reversion_detail": {"enabled": bool(CONFIG.get("ENGINE_A_MEAN_REVERSION", {}).get("ENABLED", False))},
        "session_multiplier": 1.0,
        "equity_session_multiplier": 1.0,
        "volatility_regime_multiplier": 1.0,
        "conviction": 0.0,
        "insufficient_factors": True,
        "indeterminate_direction": reason == "indeterminate_trend",
        "min_directional_failed": min_directional_failed,
        "active_directional_factors": [],
        "active_nondirectional_factors": [],
        "disabled_factors": [],
        "directional_confidence_multiplier": 0.0,
        "directional_ramp_multiplier": 0.0,
        "effective_min_directional": round(min_directional, 4),
        "min_directional_threshold": round(min_directional, 4),
        "optional_factor_coverage": 0.0,
        "missing_directional_optional_count": 0,
        "correlation_adjustments": {},
        "crypto_engine_a_diagnostics": None,
        "intermarket_confirmation": None,
        "intermarket_engine_a_delta": 0.0,
        "feed_status": feed_status,
        "btc_bias_applied": None,
        "abort_reason": reason,
        "filtered_indicators": {},
    }
