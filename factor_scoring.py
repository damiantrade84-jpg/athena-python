"""factor_scoring.py — Engine A v2: 3-Factor Quantitative Scoring Engine.

Architecture (research-validated, 2026-04):
  Factor 1 — TREND       : Multi-TF EMA alignment (D1/H4/H1). Determines direction ONLY.
  Factor 2 — MOMENTUM    : RSI + MACD confirmation quality (0-1 scale). Sizes conviction.
  Factor 3 — ADX GATE    : Trend-strength filter. Hard abort < 15; soft penalty 15-25; full >= 25.
  Addon    — ASSET ADDON : One optional secondary per class (forex=carry, crypto=funding,
                           commodity=COT). Graceful 0.0 when data unavailable — no phantom signal.
  Gate     — DI ALIGNMENT: +DI/-DI directional confirmation. Soft penalty (0.3x) when DI
           disagrees with EMA direction by >5 points. Prevents EMA/ADI divergence trades.

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
import time
import warnings
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional

from config import CONFIG
from engine_a_scoring_profile import (
    resolve_engine_a_scoring_profile,
    scoring_profile_public_dict,
    snap_for_tf,
)
from regime import detect_regime

log = logging.getLogger("athena")

# ── Constants ─────────────────────────────────────────────────────────────────

_CRYPTO_COT_PAIRS = {"BTC/USDT", "ETH/USDT"}
_funding_stats_cache: dict[str, tuple[dict, float]] = {}
_FUNDING_STATS_TTL = 3600


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
    # Code fallback is False (fail-closed), but config.yaml sets this to true
    # at runtime so per-group indicator differentiation is active in production.
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


def _apply_regime_floor_sensitivity(
    class_floor: float,
    regime: str | None,
    score_group: str | None,
    asset_type: str,
    regime_floor: float,
) -> float:
    """Re-apply the RANGING/HIGH_VOLATILITY floor reduction for regime-sensitive classes.

    An explicit ENGINE_A_CONVICTION_FLOOR_BY_CLASS entry otherwise SUPERSEDES the
    regime-conditional floor, so weak momentum in noisy regimes would count as much
    as in a clean trend. For classes listed in
    ENGINE_A_CONVICTION_FLOOR_REGIME_SENSITIVE_CLASSES we instead take the lower of
    the class floor and the regime floor (never raising it), restoring the
    noisy-regime skepticism. Forex is deliberately omitted so its strict flat floor
    keeps score reachability. Only lowers; classes not listed are unchanged.
    """
    if regime not in {"RANGING", "HIGH_VOLATILITY"}:
        return float(class_floor)
    sensitive = set(CONFIG.get("ENGINE_A_CONVICTION_FLOOR_REGIME_SENSITIVE_CLASSES") or [])
    if (score_group in sensitive) or (asset_type in sensitive):
        return min(float(class_floor), float(regime_floor))
    return float(class_floor)


def _resolve_adx_source_mode(score_group: str | None, asset_type: str) -> str:
    keyed = CONFIG.get("ENGINE_A_ADX_SOURCE_BY_CLASS") or {}
    mode = _resolve_class_keyed(keyed, score_group, asset_type, "d1_first")
    mode = str(mode or "d1_first").strip().lower()
    if mode not in {"d1_first", "h4_first", "max"}:
        return "d1_first"
    return mode


def _resolve_di_alignment_multipliers(score_group: str | None, asset_type: str) -> dict:
    multipliers = {"missing": 0.5, "balanced": 0.5, "opposed": 0.5, "severe_opposed": 0.35}
    if not _engine_a_group_adjustments_enabled():
        return multipliers

    keyed = CONFIG.get("ENGINE_A_DI_ALIGNMENT_MULT_BY_CLASS") or {}
    overrides = _resolve_class_keyed(keyed, score_group, asset_type, {})
    if isinstance(overrides, dict):
        for state in ("missing", "balanced", "opposed", "severe_opposed"):
            if state in overrides:
                multipliers[state] = _float_cfg(overrides.get(state), multipliers[state])
    return {state: max(0.0, min(1.0, value)) for state, value in multipliers.items()}


def _resolve_di_alignment_gap_thresholds() -> tuple[float, float]:
    """Resolve the |+DI - -DI| gap thresholds that segment the DI alignment
    multiplier tiers. Returns (balanced, opposed) where balanced < opposed.
    Defaults (5.0, 15.0) preserve the prior hardcoded ramp exactly. Read from
    ENGINE_A_DI_ALIGNMENT_GAP_THRESHOLDS so calibration can move the
    balanced/opposed boundaries without code changes."""
    raw = CONFIG.get("ENGINE_A_DI_ALIGNMENT_GAP_THRESHOLDS") or {}
    balanced = _float_cfg(raw.get("BALANCED"), 5.0)
    opposed = _float_cfg(raw.get("OPPOSED"), 15.0)
    if balanced <= 0.0 or opposed <= balanced:
        return 5.0, 15.0
    return balanced, opposed


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


# ── Group-aware indicator periods (2026 calibration review fix) ──────────────
# Extends the proven _resolve_class_keyed + ENGINE_A_SCORE_GROUP_ADJUSTMENTS_ENABLED
# pattern to core periods/lengths that feed the 3 factors. This directly addresses
# the "universal + light adjustment only" shortfall identified in the expert review.
# Behavior is 100% unchanged when the flag is false (or keys absent). Research Lab
# + proposed_calibration_overlay.yaml are the intended future source for differentiated values.
# All 20+ ENGINE_A_KNOWN_SCORE_GROUPS must eventually have explicit entries in config.


def _resolve_ema_periods(score_group: str | None, asset_type: str) -> dict:
    """Return EMA periods for the multi-TF trend factor (D1 long, H4/H1 momentum/entry)."""
    defaults = {"trend": 21, "long": 200, "momentum": 50}
    if not _engine_a_group_adjustments_enabled():
        return defaults

    keyed = CONFIG.get("ENGINE_A_EMA_PERIODS_BY_CLASS") or {}
    overrides = _resolve_class_keyed(keyed, score_group, asset_type, {})
    if isinstance(overrides, dict):
        for k in ("trend", "long", "momentum"):
            if k in overrides:
                try:
                    defaults[k] = int(overrides[k])
                except (TypeError, ValueError):
                    pass
    return defaults


def _resolve_rsi_period(score_group: str | None, asset_type: str) -> int:
    """Return RSI period used in momentum factor."""
    period = 14
    if not _engine_a_group_adjustments_enabled():
        return period

    keyed = CONFIG.get("ENGINE_A_RSI_PERIOD_BY_CLASS") or {}
    override = _resolve_class_keyed(keyed, score_group, asset_type, period)
    try:
        return int(override)
    except (TypeError, ValueError):
        return period


def _resolve_atr_adx_periods(score_group: str | None = None, asset_type: str = "") -> dict:
    """Return ATR/ADX periods Engine A scores with.

    Single source of truth shared with the chart API (_resolve_chart_indicator_periods)
    so Engine A and the chart surface cannot drift independently. Defaults to the
    Wilder standard (14/14). Per-group overrides take effect only when
    ENGINE_A_SCORE_GROUP_ADJUSTMENTS_ENABLED is true and a
    ENGINE_A_ATR_ADX_PERIODS_BY_CLASS entry resolves for the class — mirroring
    _resolve_ema_periods / _resolve_macd_params. With the flag off or no
    override, behavior is identical to the historical hard-coded 14/14.
    """
    defaults = {"atr": 14, "adx": 14}
    if not _engine_a_group_adjustments_enabled():
        return defaults

    keyed = CONFIG.get("ENGINE_A_ATR_ADX_PERIODS_BY_CLASS") or {}
    overrides = _resolve_class_keyed(keyed, score_group, asset_type, {})
    if isinstance(overrides, dict):
        for k in ("atr", "adx"):
            if k in overrides:
                try:
                    defaults[k] = int(overrides[k])
                except (TypeError, ValueError):
                    pass
    return defaults


def _resolve_macd_params(score_group: str | None, asset_type: str) -> dict:
    """Return MACD (fast, slow, signal) tuple for momentum factor."""
    params = {"fast": 12, "slow": 26, "signal": 9}
    if not _engine_a_group_adjustments_enabled():
        return params

    keyed = CONFIG.get("ENGINE_A_MACD_PARAMS_BY_CLASS") or {}
    overrides = _resolve_class_keyed(keyed, score_group, asset_type, {})
    if isinstance(overrides, dict):
        for k in ("fast", "slow", "signal"):
            if k in overrides:
                try:
                    params[k] = int(overrides[k])
                except (TypeError, ValueError):
                    pass
    return params


# Intraday rungs that take ENGINE_A_ENTRY_TF_PERIODS instead of the H4/D1-scale
# ENGINE_A_*_BY_CLASS periods. M5 is included: leaving it out did not disable M5
# scoring, it silently computed M5 snapshots with H4 periods (RSI 18, MACD
# 12/26, EMA 26 on five-minute bars). The configured rows are calibrated for
# M15/M30 and are merely the closest available for M5 — a dedicated M5 row is
# still a calibration task, not an assumption this constant should make.
ENTRY_TF_PERIOD_OVERRIDE_TFS = frozenset({"M30", "M15", "M5"})
# Setup-rung candidate under the universal policy (setup=H1). Only applied when
# ENGINE_A_SETUP_TF_PERIODS_ENABLED is true *and* a nested H1 row exists for the
# score group — no invented H1 periods when the flag is off.
SETUP_TF_PERIOD_CANDIDATE_TFS = frozenset({"H1"})
_NESTED_ENTRY_TF_KEYS = frozenset({"M5", "M15", "M30", "H1"})


def _setup_tf_periods_enabled() -> bool:
    """Evidence gate: H1 setup periods stay off until calibration is approved."""
    try:
        return bool(CONFIG.get("ENGINE_A_SETUP_TF_PERIODS_ENABLED", False))
    except Exception:
        return False


def entry_tf_uses_period_overrides(tf: str | None) -> bool:
    """Whether ``tf`` may take ENGINE_A_ENTRY_TF_PERIODS instead of base class periods."""
    key = str(tf or "").upper()
    if key in ENTRY_TF_PERIOD_OVERRIDE_TFS:
        return True
    if key in SETUP_TF_PERIOD_CANDIDATE_TFS and _setup_tf_periods_enabled():
        return True
    return False


def _base_indicator_periods(score_group: str | None, asset_type: str) -> dict[str, int]:
    """Bundle resolved group periods into the _calc_indicator_bundle key shape."""
    ema = _resolve_ema_periods(score_group, asset_type)
    macd = _resolve_macd_params(score_group, asset_type)
    aa = _resolve_atr_adx_periods(score_group, asset_type)
    return {
        "ema_trend": int(ema.get("trend", 21)),
        "ema_momentum": int(ema.get("momentum", 50)),
        "ema_long": int(ema.get("long", 200)),
        "rsi": int(_resolve_rsi_period(score_group, asset_type)),
        "macd_fast": int(macd.get("fast", 12)),
        "macd_slow": int(macd.get("slow", 26)),
        "macd_signal": int(macd.get("signal", 9)),
        "atr": int(aa.get("atr", 14)),
        "adx": int(aa.get("adx", 14)),
    }


def _flat_entry_period_fields(row: Mapping[str, Any]) -> dict[str, Any]:
    """Strip nested TF sub-rows so flat legacy keys remain mergeable."""
    return {
        key: value
        for key, value in row.items()
        if str(key).upper() not in _NESTED_ENTRY_TF_KEYS
    }


def _resolve_entry_tf_period_override(
    score_group: str | None,
    asset_type: str,
    tf: str | None = None,
) -> dict | None:
    """Raw ENGINE_A_ENTRY_TF_PERIODS fields for score_group (+ optional TF), or None.

    Resolution order for a requested TF:
    1. Nested ``ENGINE_A_ENTRY_TF_PERIODS[group][TF]`` when present
       (H1 only when ``ENGINE_A_SETUP_TF_PERIODS_ENABLED``).
    2. Flat legacy keys on the group row for M5/M15/M30 only.
    3. None → caller keeps base class periods.

    Unlike other class maps, missing score_group does not inherit the ``default``
    entry row — callers fall back to base ENGINE_A_*_BY_CLASS periods.
    """
    del asset_type  # entry map is score-group keyed only
    if not _engine_a_group_adjustments_enabled():
        return None
    keyed = CONFIG.get("ENGINE_A_ENTRY_TF_PERIODS") or {}
    if not score_group or score_group not in keyed:
        return None
    row = keyed.get(score_group)
    if not isinstance(row, dict) or not row:
        return None
    tf_key = str(tf or "").upper()
    flat = _flat_entry_period_fields(row)
    if tf_key:
        nested = row.get(tf_key)
        if isinstance(nested, dict) and nested:
            if tf_key == "H1" and not _setup_tf_periods_enabled():
                return None
            return dict(nested)
        if tf_key in ENTRY_TF_PERIOD_OVERRIDE_TFS:
            return flat if flat else None
        # When setup-TF periods are enabled, H1 setup reuses the group's flat
        # entry pack until a nested H1 row is calibrated.
        if tf_key == "H1" and _setup_tf_periods_enabled():
            return flat if flat else None
        return None
    # Legacy callers (no TF): return flat fields only.
    return flat if flat else None


def _merge_entry_tf_periods(
    base: Mapping[str, int], override: Mapping[str, Any]
) -> dict[str, int]:
    """Apply entry-TF override onto base periods; ema_long stays from base."""
    out = dict(base)
    if "ema_trend" in override:
        try:
            out["ema_trend"] = int(override["ema_trend"])
        except (TypeError, ValueError):
            pass
    if "ema_mom" in override:
        try:
            out["ema_momentum"] = int(override["ema_mom"])
        except (TypeError, ValueError):
            pass
    if "rsi" in override:
        try:
            out["rsi"] = int(override["rsi"])
        except (TypeError, ValueError):
            pass
    macd_raw = override.get("macd")
    if isinstance(macd_raw, (list, tuple)) and len(macd_raw) >= 3:
        try:
            out["macd_fast"] = int(macd_raw[0])
            out["macd_slow"] = int(macd_raw[1])
            out["macd_signal"] = int(macd_raw[2])
        except (TypeError, ValueError):
            pass
    elif isinstance(macd_raw, dict):
        for src, dst in (("fast", "macd_fast"), ("slow", "macd_slow"), ("signal", "macd_signal")):
            if src in macd_raw:
                try:
                    out[dst] = int(macd_raw[src])
                except (TypeError, ValueError):
                    pass
    if "atr" in override:
        try:
            out["atr"] = int(override["atr"])
        except (TypeError, ValueError):
            pass
    if "adx" in override:
        try:
            out["adx"] = int(override["adx"])
        except (TypeError, ValueError):
            pass
    return out


def _resolved_indicator_periods_for_tf(
    score_group: str | None, asset_type: str, tf: str
) -> dict[str, int]:
    """Period dict for one TF: entry/setup overrides when the TF is eligible."""
    base = _base_indicator_periods(score_group, asset_type)
    tf_key = str(tf or "").upper()
    if not entry_tf_uses_period_overrides(tf_key):
        return base
    override = _resolve_entry_tf_period_override(score_group, asset_type, tf_key)
    if not override:
        return base
    return _merge_entry_tf_periods(base, override)


# ADX gate thresholds (Wilder 1978 standard) — tunable via config.yaml FACTOR_ADX_*
# NOTE: Read lazily inside _adx_gate() so config reloads take effect without restart.
_ADX_HARD_FAIL_DEFAULT = 15.0    # below this → dead market, abort
_ADX_SOFT_MULT_DEFAULT = 0.65    # multiplier in soft zone

# Track warnings by key so one missing class does not suppress diagnostics for
# another missing class later in the same process.
_adx_fallback_warned_keys: set[tuple[str, str | None]] = set()
_adx_invalid_range_warned_keys: set[tuple[float, float]] = set()
_cot_formula_exception_warned = False

# LRU cache for _previous_indicator_snap to avoid recomputing the full indicator
# suite on N-1 candles just to get the prior bar's EMA values for hysteresis.
_PREV_SNAP_CACHE_MAX = 200
_prev_snap_cache: dict = {}

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


# ── Backward-compat stub (used by scoring.py) ─────────────────────────────────

def build_oi_context_for_factor_scoring(
    oi_data: Optional[dict],
    d1_candles: List,
    h1_snap: Optional[dict],
) -> Optional[dict]:
    """Build oi_context for crypto derivatives addon (parity with old engine)."""
    if not oi_data or oi_data.get("oiChange") is None:
        return None
    if bool(oi_data.get("error")):
        return None
    ts = oi_data.get("ts")
    if ts is None and not bool(oi_data.get("point_in_time")):
        return None
    if ts is not None and not bool(oi_data.get("point_in_time")):
        try:
            max_age = float(CONFIG.get("ENGINE_A_OI_MAX_AGE_SEC", 300) or 300)
            if max_age > 0 and time.time() - float(ts) > max_age:
                return None
        except (TypeError, ValueError):
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
    ctx = {
        "oi_change_pct": float(oi_data["oiChange"]),
        "price_change_pct": (cur - prev) / prev * 100.0,
    }
    if oi_data.get("oiChangeSmoothed") is not None:
        try:
            ctx["oi_change_pct_smoothed"] = float(oi_data["oiChangeSmoothed"])
        except (TypeError, ValueError):
            pass
    return ctx


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
    if _gap > _ema_cross_min_gap():
        return 1.0 if cur_long else (-1.0 if cur_short else None)
    return None


def _ema_cross_min_gap() -> float:
    try:
        return max(0.0, float(CONFIG.get("ENGINE_A_EMA_CROSS_MIN_GAP", 0.001) or 0.001))
    except (TypeError, ValueError):
        return 0.001


def _fresh_ema_cross_sign(
    current_snap: dict,
    prev_snap: dict | None,
    fast_key: str,
    slow_key: str,
) -> float | None:
    """Return +/-1 only on the fresh-cross bar (current relation != previous)."""
    if not isinstance(current_snap, dict) or not isinstance(prev_snap, dict):
        return None
    fast_cur = current_snap.get(fast_key)
    slow_cur = current_snap.get(slow_key)
    fast_prev = prev_snap.get(fast_key)
    slow_prev = prev_snap.get(slow_key)
    if fast_cur is None or slow_cur is None or fast_prev is None or slow_prev is None:
        return None
    try:
        fast_cur = float(fast_cur)
        slow_cur = float(slow_cur)
        fast_prev = float(fast_prev)
        slow_prev = float(slow_prev)
    except (TypeError, ValueError):
        return None
    if slow_cur == 0 or slow_prev == 0:
        return None

    cur_long = fast_cur > slow_cur
    cur_short = fast_cur < slow_cur
    prev_long = fast_prev > slow_prev
    prev_short = fast_prev < slow_prev
    if cur_long and prev_long:
        return None
    if cur_short and prev_short:
        return None
    if cur_long and not prev_long:
        return 1.0
    if cur_short and not prev_short:
        return -1.0
    return None


def _apply_reversal_pending_overlay(
    trend_score: float,
    direction: str | None,
    detail: dict,
    *,
    tf_specs: list[tuple[dict, dict | None, str, str]],
) -> tuple[float, str | None, dict]:
    if not bool(CONFIG.get("ENGINE_A_REVERSAL_PENDING_ENABLED", False)):
        return trend_score, direction, detail

    try:
        min_tf = int(CONFIG.get("ENGINE_A_REVERSAL_PENDING_MIN_TF", 2) or 2)
    except (TypeError, ValueError):
        min_tf = 2
    min_tf = max(1, min_tf)

    fresh_long = 0
    fresh_short = 0
    for cur_snap, prev_snap, fast_key, slow_key in tf_specs:
        sign = _fresh_ema_cross_sign(cur_snap, prev_snap, fast_key, slow_key)
        if sign is not None and sign > 0:
            fresh_long += 1
        elif sign is not None and sign < 0:
            fresh_short += 1

    pending_dir = None
    if fresh_long >= min_tf and fresh_short < min_tf:
        pending_dir = "LONG"
    elif fresh_short >= min_tf and fresh_long < min_tf:
        pending_dir = "SHORT"
    if not pending_dir:
        return trend_score, direction, detail

    detail = dict(detail)
    detail["reversal_pending"] = True
    detail["reversal_pending_direction"] = pending_dir
    confirmed = str(direction or "").upper()
    if confirmed in ("LONG", "SHORT"):
        if confirmed != pending_dir:
            detail["reversal_pending_opposes_confirmed"] = True
        return trend_score, direction, detail

    detail["direction_status"] = "reversal_pending"
    return 0.0, pending_dir, detail


def _trend_magnitude_cfg() -> dict:
    cfg = CONFIG.get("ENGINE_A_TREND_MAGNITUDE") or {}
    return cfg if isinstance(cfg, dict) else {}


def _trend_vote_strength(snap: dict, fast_key: str, slow_key: str) -> float | None:
    """ATR-normalized EMA separation in [0, 1] for trend magnitude scaling.

    tanh(|fast - slow| / (ATR_K * atr)): a barely-crossed EMA pair (gap << ATR)
    scores near 0; an established trend (gap >= ~2 ATR at default ATR_K=2.0)
    saturates near 1. Returns None when ATR or either EMA is unavailable so the
    vote falls back to sign-only behaviour.
    """
    cfg = _trend_magnitude_cfg()
    fast_raw = snap.get(fast_key)
    slow_raw = snap.get(slow_key)
    atr_raw = snap.get("atr")
    if fast_raw is None or slow_raw is None or atr_raw is None:
        return None
    try:
        fast = float(fast_raw)
        slow = float(slow_raw)
        atr = float(atr_raw)
    except (TypeError, ValueError):
        return None
    if atr <= 0 or not math.isfinite(atr):
        return None
    atr_k = _float_cfg(cfg.get("ATR_K"), 2.0)
    if atr_k <= 0:
        atr_k = 2.0
    return math.tanh(abs(fast - slow) / (atr_k * atr))


def _coherent_trend_score_legacy(
    d1_snap: dict, h4_snap: dict, h1_snap: dict, asset_type: str,
    d1_prev: dict | None = None, h4_prev: dict | None = None, h1_prev: dict | None = None,
    score_group: str | None = None,
) -> tuple:
    """Legacy multi-TF trend score using INDICATOR_WEIGHTS class-keyed weights."""
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
    vote_strengths = {}

    d1_sign = _ema_cross_confirmed(d1_snap, d1_prev, "ema21", "ema200")
    if d1_sign is not None:
        votes.append(("d1_ema_trend", d1_sign, _w("d1_ema_trend")))
        detail["d1"] = "LONG" if d1_sign > 0 else "SHORT"
        vote_strengths["d1_ema_trend"] = _trend_vote_strength(d1_snap, "ema21", "ema200")
    elif d1_snap.get("ema21") is not None and d1_snap.get("ema200") is None:
        detail["d1_ema200_missing"] = True
    elif d1_snap.get("ema21") is not None and d1_snap.get("ema200") is not None:
        detail["d1_hysteresis_pending"] = True

    h4_sign = _ema_cross_confirmed(h4_snap, h4_prev, "ema21", "ema50")
    if h4_sign is not None:
        votes.append(("h4_ema_trend", h4_sign, _w("h4_ema_trend")))
        detail["h4"] = "LONG" if h4_sign > 0 else "SHORT"
        vote_strengths["h4_ema_trend"] = _trend_vote_strength(h4_snap, "ema21", "ema50")
    elif h4_snap.get("ema21") is not None and h4_snap.get("ema50") is not None:
        detail["h4_hysteresis_pending"] = True

    h1_sign = _ema_cross_confirmed(h1_snap, h1_prev, "ema21", "ema50")
    if h1_sign is not None:
        votes.append(("ema_trend", h1_sign, _w("ema_trend")))
        detail["h1"] = "LONG" if h1_sign > 0 else "SHORT"
        vote_strengths["ema_trend"] = _trend_vote_strength(h1_snap, "ema21", "ema50")
    elif h1_snap.get("ema21") is not None and h1_snap.get("ema50") is not None:
        detail["h1_hysteresis_pending"] = True

    return _apply_reversal_pending_overlay(
        *_finalize_coherent_trend_score(votes, detail, vote_strengths=vote_strengths),
        tf_specs=[
            (d1_snap, d1_prev, "ema21", "ema200"),
            (h4_snap, h4_prev, "ema21", "ema50"),
            (h1_snap, h1_prev, "ema21", "ema50"),
        ],
    )


def _coherent_trend_score_from_profile(
    d1_snap: dict,
    h4_snap: dict,
    h1_snap: dict,
    *,
    d1_prev: dict | None,
    h4_prev: dict | None,
    h1_prev: dict | None,
    scoring_profile: dict,
) -> tuple:
    """Multi-TF trend score driven by ENGINE_A_SCORING_PROFILE layers/weights."""
    snaps = {"D1": d1_snap, "H4": h4_snap, "H1": h1_snap}
    prevs = {"D1": d1_prev, "H4": h4_prev, "H1": h1_prev}
    trend_weights = scoring_profile.get("trend_weights") or {}
    layers = scoring_profile.get("trend_layers") or []

    def _w(key):
        try:
            return float(trend_weights.get(key, 0.0))
        except (TypeError, ValueError):
            return 0.0

    votes = []
    detail = {"scoring_profile_style": scoring_profile.get("style")}
    vote_strengths = {}
    for layer in layers:
        tf = str(layer.get("tf") or "").upper()
        snap = snaps.get(tf) or {}
        prev = prevs.get(tf)
        weight_key = str(layer.get("weight_key") or "")
        fast_ema = str(layer.get("fast_ema") or "ema21")
        slow_ema = str(layer.get("slow_ema") or "ema50")
        sign = _ema_cross_confirmed(snap, prev, fast_ema, slow_ema)
        tf_key = tf.lower()
        if sign is not None:
            votes.append((weight_key, sign, _w(weight_key)))
            detail[tf_key] = "LONG" if sign > 0 else "SHORT"
            vote_strengths[weight_key] = _trend_vote_strength(snap, fast_ema, slow_ema)
        elif snap.get(fast_ema) is not None and snap.get(slow_ema) is None:
            detail[f"{tf_key}_{slow_ema}_missing"] = True
        elif snap.get(fast_ema) is not None and snap.get(slow_ema) is not None:
            detail[f"{tf_key}_hysteresis_pending"] = True

    detail["trend_timeframes"] = list(scoring_profile.get("trend_timeframes") or [])
    tf_specs = []
    for layer in layers:
        tf = str(layer.get("tf") or "").upper()
        snap = snaps.get(tf) or {}
        prev = prevs.get(tf)
        fast_ema = str(layer.get("fast_ema") or "ema21")
        slow_ema = str(layer.get("slow_ema") or "ema50")
        tf_specs.append((snap, prev, fast_ema, slow_ema))
    return _apply_reversal_pending_overlay(
        *_finalize_coherent_trend_score(votes, detail, vote_strengths=vote_strengths),
        tf_specs=tf_specs,
    )


def _finalize_coherent_trend_score(
    votes: list, detail: dict, vote_strengths: dict | None = None
) -> tuple:
    """Shared vote aggregation for legacy and profile-driven trend scoring.

    ``vote_strengths`` maps weight_key -> ATR-normalized EMA separation in
    [0, 1] (or None when unavailable). Only applied when
    ENGINE_A_TREND_MAGNITUDE.ENABLED is true; direction, tie handling, and
    coherence semantics are unchanged — magnitude alone is scaled.
    """
    if not votes:
        return 0.0, None, {"error": "no_ema_data", **detail}

    long_w = sum(w for _, d, w in votes if d > 0)
    short_w = sum(w for _, d, w in votes if d < 0)
    total_w = long_w + short_w
    if total_w <= 0:
        return 0.0, None, {"error": "zero_weight", **detail}

    if abs(long_w - short_w) < 1e-9:
        # Perfect weighted tie exits before COHERENCE_RATIO_FLOOR: no direction.
        detail["weighted_tf_tie"] = True
        detail["long_weight"] = round(long_w, 4)
        detail["short_weight"] = round(short_w, 4)
        detail["weighted_balance"] = 0.0
        detail["vote_components"] = [
            {
                "component": name,
                "direction": "LONG" if d > 0 else "SHORT",
                "weight": round(w, 4),
            }
            for name, d, w in votes
        ]
        return 0.0, None, {"error": "weighted_tf_tie", **detail}
    dominant_sign = 1.0 if long_w > short_w else -1.0

    dominant_w = long_w if dominant_sign > 0 else short_w
    # Use weighted vote margin as coherence, not dominant-side share. A near-tie
    # should remain low confidence; a perfect tie exits above with no direction.
    # COHERENCE_RATIO_FLOOR applies only after a non-tied side exists.
    _coh_floor = float(CONFIG.get("COHERENCE_RATIO_FLOOR", 0.0))
    _coh_floor = max(0.0, min(1.0, _coh_floor))
    weighted_margin = abs(long_w - short_w) / total_w
    try:
        _min_margin = float(CONFIG.get("ENGINE_A_DIRECTION_MIN_MARGIN", 0.0) or 0.0)
    except (TypeError, ValueError):
        _min_margin = 0.0
    if _min_margin > 0 and weighted_margin < _min_margin:
        detail["weighted_margin_below_min"] = True
        detail["direction_min_margin"] = _min_margin
        detail["long_weight"] = round(long_w, 4)
        detail["short_weight"] = round(short_w, 4)
        detail["weighted_balance"] = round((long_w - short_w) / total_w, 4)
        detail["vote_components"] = [
            {
                "component": name,
                "direction": "LONG" if d > 0 else "SHORT",
                "weight": round(w, 4),
            }
            for name, d, w in votes
        ]
        return 0.0, None, {"error": "direction_margin_below_min", **detail}
    coherence_ratio = max(_coh_floor, min(1.0, weighted_margin))
    agreement_count = sum(1 for _, d, _ in votes if d == dominant_sign)
    # Scale by TF coverage so a single available TF cannot produce a full 3.0 score.
    # D1 only → max 1.0; D1+H4 → max 2.0; all three → max 3.0.
    # With one active vote, coherence is full but coverage caps magnitude at 1.0.
    active_votes = len(votes)
    if active_votes == 1:
        vote_weights = [w for _, _, w in votes]
        max_single_weight = max(vote_weights) if vote_weights else dominant_w
        if max_single_weight <= 0:
            max_single_weight = dominant_w
        dominant_weight = dominant_w
        _tf_coverage = (1.0 / 3.0) * (dominant_weight / max_single_weight)
    else:
        _tf_coverage = active_votes / 3.0
    magnitude = coherence_ratio * 3.0 * _tf_coverage

    # Optional ATR-normalized magnitude scaling (config-gated, default off).
    # Sign-only votes score a barely-crossed EMA stack the same as an
    # established trend; this scales magnitude by the weighted average
    # separation strength of the agreeing TFs. MIN_MULT keeps a confirmed
    # cross from being zeroed outright by a tiny gap.
    _mag_cfg = _trend_magnitude_cfg()
    if bool(_mag_cfg.get("ENABLED", False)) and vote_strengths:
        _strength_pairs = [
            (vote_strengths.get(name), w)
            for name, d, w in votes
            if d == dominant_sign and vote_strengths.get(name) is not None and w > 0
        ]
        if _strength_pairs:
            _sw_total = sum(w for _, w in _strength_pairs)
            if _sw_total > 0:
                _avg_strength = sum(s * w for s, w in _strength_pairs) / _sw_total
                _min_mult = _float_cfg(_mag_cfg.get("MIN_MULT"), 0.25)
                _min_mult = max(0.0, min(1.0, _min_mult))
                _mag_mult = _min_mult + (1.0 - _min_mult) * _avg_strength
                magnitude *= _mag_mult
                detail["trend_magnitude_mult"] = round(_mag_mult, 4)
                detail["trend_magnitude_avg_strength"] = round(_avg_strength, 4)

    trend_score = dominant_sign * magnitude
    direction = "LONG" if dominant_sign > 0 else "SHORT"

    detail.update({
        "agreement_count": agreement_count,
        "agreement_components": [
            name for name, d, _ in votes if d == dominant_sign
        ],
        "vote_components": [
            {
                "component": name,
                "direction": "LONG" if d > 0 else "SHORT",
                "weight": round(w, 4),
            }
            for name, d, w in votes
        ],
        "total_count": len(votes),
        "coherence_ratio": round(coherence_ratio, 4),
        "tf_coverage": round(_tf_coverage, 4),
        "dominant_direction": direction,
        "weighted_balance": round((long_w - short_w) / total_w, 4),
    })
    return trend_score, direction, detail


def _coherent_trend_score(
    d1_snap: dict, h4_snap: dict, h1_snap: dict, asset_type: str,
    d1_prev: dict | None = None, h4_prev: dict | None = None, h1_prev: dict | None = None,
    score_group: str | None = None,
    scoring_profile: dict | None = None,
) -> tuple:
    """Multi-TF EMA alignment trend score.

    When ``scoring_profile`` is enabled, uses ENGINE_A_SCORING_PROFILE layers/weights.
    Otherwise falls back to legacy INDICATOR_WEIGHTS class-keyed resolution.
    """
    if scoring_profile and scoring_profile.get("enabled"):
        return _coherent_trend_score_from_profile(
            d1_snap,
            h4_snap,
            h1_snap,
            d1_prev=d1_prev,
            h4_prev=h4_prev,
            h1_prev=h1_prev,
            scoring_profile=scoring_profile,
        )
    return _coherent_trend_score_legacy(
        d1_snap,
        h4_snap,
        h1_snap,
        asset_type,
        d1_prev=d1_prev,
        h4_prev=h4_prev,
        h1_prev=h1_prev,
        score_group=score_group,
    )


def _previous_indicator_snap(
    candles: list | None,
    pair_id: str,
    tf: str,
    score_group: str | None = None,
    asset_type: str = "other",
) -> dict | None:
    """Return the prior confirmed indicator snapshot for EMA hysteresis.

    Must use the same indicator entry point as live ``analyze_pair`` (which calls
    ``calc_indicators_with_normalized`` with ``score_group``). Using only
    ``calc_indicators_for_engine_a`` on bar N-1 while the current bar stayed on
    the universal path broke 2-bar EMA confirmation and zeroed forex trend votes.

    Results are cached per (pair_id, tf, len(candles), candles[-2]['close'])
    to avoid recomputing the full indicator suite when scanning many pairs.
    """
    if not isinstance(candles, list) or len(candles) < 2:
        return None
    try:
        _prev_close = candles[-2].get("close")
        _cache_key = (pair_id, tf, len(candles), _prev_close)
        _cached = _prev_snap_cache.get(_cache_key)
        if _cached is not None:
            return _cached

        from indicators import calc_indicators_with_normalized

        prev_indicators = calc_indicators_with_normalized(
            candles[:-1],
            asset_type or "other",
            score_group=score_group,
        )
        prev_snap = prev_indicators.get("snap") if isinstance(prev_indicators, dict) else None
        result = prev_snap if isinstance(prev_snap, dict) else None

        _prev_snap_cache[_cache_key] = result
        if len(_prev_snap_cache) > _PREV_SNAP_CACHE_MAX:
            # Evict oldest entry (Python 3.7+ dict preserves insertion order)
            _oldest_key = next(iter(_prev_snap_cache))
            del _prev_snap_cache[_oldest_key]

        return result
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


def _momentum_smoothing_cfg() -> dict:
    cfg = CONFIG.get("ENGINE_A_MOMENTUM_SMOOTHING") or {}
    return cfg if isinstance(cfg, dict) else {}


def _rsi_score_smooth(rsi: float, is_long: bool, ob: float, os_: float, width: float) -> float:
    """Piecewise-linear RSI confirmation score without step discontinuities.

    Plateau values match the step logic in _momentum_quality exactly
    (0.0 / 0.30 / 0.50 / -0.25); ``width`` half-width linear ramps replace the
    jumps at os_, 50, and ob so feed noise at a boundary cannot flip the score
    discontinuously. SHORT is scored by reflecting RSI about 50.
    """
    if not is_long:
        rsi = 100.0 - rsi
        ob, os_ = 100.0 - os_, 100.0 - ob
    # Clamp ramp width so adjacent ramps cannot overlap.
    width = max(0.0, min(width, (50.0 - os_) / 2.0, (ob - 50.0) / 2.0))
    if width <= 0:
        # Degenerate bounds — fall back to plateau steps.
        if rsi >= ob:
            return -0.25
        if rsi >= 50:
            return 0.50
        if rsi >= os_:
            return 0.30
        return 0.0
    edges = ((os_, 0.0, 0.30), (50.0, 0.30, 0.50), (ob, 0.50, -0.25))
    for edge, lo_val, hi_val in edges:
        if rsi < edge - width:
            return lo_val
        if rsi <= edge + width:
            t = (rsi - (edge - width)) / (2.0 * width)
            return lo_val + t * (hi_val - lo_val)
    return -0.25


def _macd_score_smooth(hist: float, is_long: bool, atr: float | None, eps_atr: float) -> float:
    """MACD histogram confirmation score with a linear ramp through zero.

    Outside +/- eps (eps = eps_atr * ATR) the plateau values match the step
    logic (+0.50 aligned / -0.25 opposed). Inside, a linear ramp removes the
    sign-flip discontinuity at hist == 0. Falls back to step behaviour when
    ATR is unavailable.
    """
    h = hist if is_long else -hist
    eps = None
    if atr is not None and atr > 0 and eps_atr > 0:
        eps = eps_atr * atr
    if eps is None or eps <= 0:
        if h > 0:
            return 0.50
        if h < 0:
            return -0.25
        return 0.0
    if h >= eps:
        return 0.50
    if h <= -eps:
        return -0.25
    return -0.25 + ((h + eps) / (2.0 * eps)) * 0.75


def _momentum_quality(
    h4_snap: dict,
    direction: str,
    asset_type: str,
    score_group: str | None = None,
    h4_candles: list | None = None,
) -> float:
    """RSI + MACD confirmation quality score in [-1.0, 1.0].

    Checks whether momentum indicators confirm the trend direction.
    Does NOT change direction — only sizes conviction.

    RSI contribution (0.6 weight):
      - Confirming side of 50: +0.50
      - Off-midline but inside [os_, ob]: +0.30  (was +0.10 — too punitive for
        forex pullbacks where RSI dips through 50 during a SHORT or rises
        through 50 during a LONG)
      - Extreme overbought/oversold: -0.25 (late entry)
      - Outside [os_, ob] on confirming side: 0.0 (reversal zone — let Engine B own)

    MACD contribution (0.4 weight):
      - Histogram aligned with direction: +0.50
      - Histogram opposing direction: -0.25  (was -0.50 — that doubled the
        effective penalty vs the prior calibration and crushed forex during
        normal H1/H4 momentum transitions)

    Divergence:
      - 35+ bar RSI divergence opposing direction dampens result by 0.4× (was
        a hard zero). Engine B owns structure-based reversal handling — Engine A
        treats divergence as a warning, not a disqualification.

    Final: clamp(weighted_sum / max_raw, 0.0, 1.0)
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
    # counts as fully confirming. RSI on the wrong side of 50 is partial/weak,
    # not negligible — trending forex pairs pull back through 50 frequently
    # without invalidating the higher-TF trend.
    _smooth_cfg = _momentum_smoothing_cfg()
    _smooth_enabled = bool(_smooth_cfg.get("ENABLED", False))

    rsi_score = 0.0
    rsi = h4_snap.get("rsi")
    if rsi is not None:
        try:
            rsi = float(rsi)
            if _smooth_enabled:
                # Config-gated: piecewise-linear ramps replace the plateau
                # jumps at os_/50/ob (same plateau values away from edges).
                rsi_score = _rsi_score_smooth(
                    rsi, is_long, ob, os_,
                    _float_cfg(_smooth_cfg.get("RSI_RAMP_WIDTH"), 2.0),
                )
            elif is_long:
                if rsi >= ob:
                    rsi_score = -0.25  # overbought — late entry risk
                elif rsi >= 50:
                    rsi_score = 0.50   # above midline — momentum confirming LONG
                elif rsi >= os_:
                    rsi_score = 0.30   # below midline pullback — partial confirm
                # rsi < os_: oversold on LONG is a reversal zone — Engine B handles structure;
                # leave rsi_score = 0.0 (neutral) here rather than double-counting
            else:
                if rsi <= os_:
                    rsi_score = -0.25  # oversold — late short entry risk
                elif rsi <= 50:
                    rsi_score = 0.50   # below midline — momentum confirming SHORT
                elif rsi <= ob:
                    rsi_score = 0.30   # above midline pullback — partial confirm
                # rsi > ob: overbought on SHORT is reversal zone — leave neutral
        except (TypeError, ValueError):
            pass

    # Stage 2.7: Divergence detection as a soft dampener.
    # If price makes higher highs but RSI/MACD makes lower highs (bearish div),
    # or price makes lower lows but RSI/MACD makes higher lows (bullish div),
    # momentum quality is dampened by 0.4× (not zeroed). Engine B owns
    # structure-based reversal handling; Engine A treats this as a warning.
    _divergence_detected = False
    if isinstance(h4_candles, list) and len(h4_candles) >= 35:
        try:
            from indicators import calc_rsi_divergence

            _rsi_div = calc_rsi_divergence(h4_candles, lookback=30)
            if _rsi_div == "bearish" and direction == "LONG":
                _divergence_detected = True
            elif _rsi_div == "bullish" and direction == "SHORT":
                _divergence_detected = True
        except Exception as exc:
            log.debug("[EA2] RSI divergence check error: %s", exc)

    # MACD histogram score — aligned confirms, opposing applies a moderate penalty.
    # Calibrated to -0.25 (between the old -0.15 which was too weak to register
    # and the -0.50 audit value which crushed forex during normal H1/H4 momentum
    # transitions). With macd_w=0.4 this contributes -0.10 to raw, ~20% drop
    # post-rescale — material but not annihilating.
    macd_score = 0.0
    macd_hist = h4_snap.get("macdHist")
    if macd_hist is None:
        macd_hist = h4_snap.get("macd_hist")
    if macd_hist is not None:
        try:
            hist = float(macd_hist)
            if _smooth_enabled:
                # Config-gated: ATR-normalized linear ramp through hist == 0
                # replaces the sign-flip step (same plateaus beyond +/- eps).
                _atr_for_macd = None
                try:
                    _atr_raw = h4_snap.get("atr")
                    if _atr_raw is not None:
                        _atr_for_macd = float(_atr_raw)
                except (TypeError, ValueError):
                    _atr_for_macd = None
                macd_score = _macd_score_smooth(
                    hist, is_long, _atr_for_macd,
                    _float_cfg(_smooth_cfg.get("MACD_NORM_EPS_ATR"), 0.05),
                )
            elif is_long and hist > 0:
                macd_score = 0.50
            elif not is_long and hist < 0:
                macd_score = 0.50
            elif (is_long and hist < 0) or (not is_long and hist > 0):
                macd_score = -0.25
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
    # Worst standard case: RSI=-0.25, MACD=-0.25 -> raw = -0.25 -> rescaled = -0.50 -> clamped to 0.
    # This ensures mom_quality can actually reach 1.0 when both indicators confirm.
    # Opposing momentum remains negative after rescale so it can reduce
    # conviction below neutral instead of being floored away.
    _rsi_cap = 0.50
    _macd_cap = 0.50
    _volume_cap = 0.50
    _max_raw = (
        _rsi_cap * rsi_w
        + _macd_cap * macd_w
        + _volume_cap * volume_w
    ) / total_w
    rescaled = raw / _max_raw if _max_raw != 0 else 0.0
    if _divergence_detected and rescaled > 0:
        rescaled *= 0.4
    return max(-1.0, min(1.0, rescaled))


def _research_lab_candidate_addon(
    pair: dict,
    direction: str,
    h4_candles: list,
    d1_candles: list,
    asset_type: str,
    score_group: str | None = None,
    h4_snap: dict | None = None,
    d1_snap: dict | None = None,
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
    default_snap = d1_snap if len(d1_candles or []) >= 60 else h4_snap
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
        snap_to_use = h4_snap if factor_name == "stochastic_cross" else default_snap
        val, detail = _research_factor_value(
            factor_name,
            direction,
            candles,
            bonus,
            penalty,
            cfg=cfg,
            pair=pair,
            asset_type=asset_type,
            snap=snap_to_use,
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
    snap: dict | None = None,
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
            return _research_bollinger_value(direction, candles, bonus, penalty, cfg=cfg)
        if name == "price_momentum":
            return _research_price_momentum_value(direction, candles, bonus, penalty, snap=snap)
        # Legacy factors — still callable but not in default factor list
        if name == "stochastic_cross":
            return _research_stochastic_value(direction, candles, bonus, penalty, cfg=cfg, pair=pair, asset_type=asset_type)
        if name in {"chandelier_trend", "aroon_trend"}:
            if not bool(CONFIG.get("ENGINE_A_RESEARCH_LAB_ALLOW_LEGACY_FACTORS", False)):
                return 0.0, {"signal": "unsupported_legacy", "value": 0.0}
            if name == "chandelier_trend":
                return _research_chandelier_value(direction, candles, bonus, penalty)
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


def _research_bollinger_value(
    direction: str,
    candles: list,
    bonus: float,
    penalty: float,
    *,
    cfg: dict | None = None,
) -> tuple[float, dict]:
    bb_cfg = (cfg or {}).get("BOLLINGER_TOUCH") if isinstance(cfg, dict) else None
    bb_cfg = bb_cfg if isinstance(bb_cfg, dict) else {}
    period = int(_float_cfg(bb_cfg.get("PERIOD"), 20.0))
    mult = _float_cfg(bb_cfg.get("MULT"), 2.0)
    if period < 5:
        period = 20
    closes = [float(c["close"]) for c in candles if c.get("close") is not None]
    if len(closes) < period + 5:
        return 0.0, {"signal": "missing", "value": 0.0}
    window = closes[-period:]
    mean = sum(window) / len(window)
    variance = sum((x - mean) ** 2 for x in window) / len(window)
    std = math.sqrt(max(0.0, variance))
    upper = mean + mult * std
    lower = mean - mult * std
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


def _research_price_momentum_value(
    direction: str,
    candles: list,
    bonus: float,
    penalty: float,
    *,
    snap: dict | None = None,
) -> tuple[float, dict]:
    """10-period rate of change momentum factor, normalized by volatility when gated."""
    closes = [float(c["close"]) for c in candles if c.get("close") is not None]
    if len(closes) < 15:
        return 0.0, {"signal": "missing", "value": 0.0}
    roc = (closes[-1] - closes[-11]) / abs(closes[-11]) if closes[-11] != 0 else 0.0

    # Volatility-normalized threshold
    rl_cfg = CONFIG.get("ENGINE_A_RESEARCH_LAB_FACTORS", {}) or {}
    vol_norm = bool(rl_cfg.get("ROC_VOL_NORM", False))
    
    if vol_norm:
        atr_val = None
        if isinstance(snap, dict):
            atr_val = snap.get("atr")
        if atr_val is not None and closes[-1] > 0:
            atr_pct = float(atr_val) / closes[-1]
        else:
            atr_pct = 0.02
        dynamic_threshold = max(0.005, min(0.05, atr_pct * 1.5))
    else:
        dynamic_threshold = 0.02

    if direction == "LONG" and roc > dynamic_threshold:
        return bonus, {"signal": "positive_momentum", "roc": round(roc, 4), "threshold": round(dynamic_threshold, 4), "value": bonus}
    if direction == "SHORT" and roc < -dynamic_threshold:
        return bonus, {"signal": "negative_momentum", "roc": round(roc, 4), "threshold": round(dynamic_threshold, 4), "value": bonus}
    if (direction == "LONG" and roc < -dynamic_threshold) or (direction == "SHORT" and roc > dynamic_threshold):
        return penalty, {"signal": "opposing_momentum", "roc": round(roc, 4), "threshold": round(dynamic_threshold, 4), "value": penalty}
    return 0.0, {"signal": "neutral_momentum", "roc": round(roc, 4), "threshold": round(dynamic_threshold, 4), "value": 0.0}


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

_EMA_CLUSTER_TIGHT_ATR = 0.35


def _safe_indicator_float(value) -> float | None:
    try:
        out = float(value)
        return out if math.isfinite(out) else None
    except (TypeError, ValueError):
        return None


def _select_adx_for_gate(
    d1_snap: dict, h4_snap: dict, asset_type: str, score_group: str | None
) -> tuple[float | None, str]:
    """Return (adx_value, source) for the ADX gate; source is d1/h4/missing."""
    _d1 = _safe_indicator_float(d1_snap.get("adx"))
    _h4 = _safe_indicator_float(h4_snap.get("adx"))
    if _d1 is not None and _h4 is not None:
        mode = _resolve_adx_source_mode(score_group, asset_type)
        if mode == "h4_first":
            return _h4, "h4"
        if mode == "max" and _h4 > _d1:
            return _h4, "h4"
        return _d1, "d1"
    if _d1 is not None:
        return _d1, "d1"
    if _h4 is not None:
        return _h4, "h4"
    return None, "missing"


def _resolve_adx_thresholds(asset_type: str, score_group: str | None) -> tuple[float, float]:
    """Return (trend_min, hard_fail) for ADX gate diagnostics and multiplier."""
    _trend_min_cfg = CONFIG.get("ADX_TREND_MIN_CLASS") or {}
    _hard_fail_cfg = CONFIG.get("FACTOR_ADX_HARD_FAIL_CLASS") or {}
    trend_min = _resolve_class_keyed(_trend_min_cfg, score_group, asset_type, None)
    hard_fail = _resolve_class_keyed(_hard_fail_cfg, score_group, asset_type, None)
    _used_fallback = False
    if asset_type == "crypto" and trend_min is None and hard_fail is None:
        _used_fallback = True
        trend_min = 25.0
        hard_fail = 15.0
    else:
        if trend_min is None:
            _used_fallback = True
            trend_min = 30.0
        if hard_fail is None:
            _used_fallback = True
            hard_fail = 10.0
    _warn_key = (str(asset_type), str(score_group) if score_group else None)
    if _used_fallback and _warn_key not in _adx_fallback_warned_keys:
        _adx_fallback_warned_keys.add(_warn_key)
        warnings.warn(
            f"ADX thresholds for asset_type='{asset_type}' not found in config. "
            f"Falling back to hardcoded defaults "
            f"(hard_fail={hard_fail}, trend_min={trend_min}) "
            f"for missing entries. Add ADX_TREND_MIN_CLASS and/or "
            f"FACTOR_ADX_HARD_FAIL_CLASS entries to config.yaml to silence.",
            UserWarning,
            stacklevel=2,
        )
    trend_min = float(trend_min)
    hard_fail = float(hard_fail)
    if hard_fail >= trend_min:
        _range_key = (hard_fail, trend_min)
        if _range_key not in _adx_invalid_range_warned_keys:
            _adx_invalid_range_warned_keys.add(_range_key)
            log.warning(
                "[EA2] ADX hard_fail %.4f >= trend_min %.4f for asset_type=%s score_group=%s; "
                "clamping hard_fail to keep soft zone non-binary",
                hard_fail,
                trend_min,
                asset_type,
                score_group,
            )
        hard_fail = trend_min - 5.0
    return trend_min, hard_fail


def _adx_multiplier_from_value(adx: float, trend_min: float, hard_fail: float) -> float:
    _range = trend_min - hard_fail
    if _range <= 0:
        return 1.0 if adx >= trend_min else 0.0
    return max(0.0, min(1.0, (adx - hard_fail) / _range))


def _price_vs_ema_label(close: float | None, ema: float | None) -> str | None:
    if close is None or ema is None:
        return None
    eps = abs(ema) * 1e-6
    if close > ema + eps:
        return "above"
    if close < ema - eps:
        return "below"
    return "at"


def _ema_slope_pct_from_candles(
    candles: list | None,
    period: int,
    *,
    series_cache=None,
    timeframe: str | None = None,
) -> float | None:
    if not isinstance(candles, list) or len(candles) < 11:
        return None
    try:
        ema_series = None
        idx = None
        if (
            series_cache is not None
            and timeframe is not None
            and series_cache.matches(timeframe, candles)
        ):
            # Full-series precompute; calc_ema is causal so values at indices
            # < len(candles) equal the per-prefix recompute. Index explicitly
            # against len(candles) -- never the shared array's tail.
            ema_series = series_cache.calc_ema_series_view(timeframe, len(candles), period)
            if ema_series is not None:
                idx = len(candles) - 1
        if ema_series is None:
            from indicators import calc_ema

            closes = [float(c["close"]) for c in candles]
            ema_series = calc_ema(closes, period)
            idx = len(ema_series) - 1
        if idx < 10 or not ema_series[idx] or not ema_series[idx - 10]:
            return None
        return round((ema_series[idx] - ema_series[idx - 10]) / ema_series[idx - 10] * 100, 3)
    except Exception:
        return None


def _forex_ema_cluster_diagnostics(
    h4_snap: dict,
    h4_candles: list | None,
    direction: str,
    *,
    series_cache=None,
    timeframe: str | None = None,
) -> dict:
    """H4 EMA-stack location diagnostics for Engine A forex (report-only by default)."""
    close = _safe_indicator_float(h4_snap.get("close"))
    atr = _safe_indicator_float(h4_snap.get("atr"))
    ema21 = _safe_indicator_float(h4_snap.get("ema21"))
    ema50 = _safe_indicator_float(h4_snap.get("ema50"))
    ema200 = _safe_indicator_float(h4_snap.get("ema200"))

    ema21_slope = _ema_slope_pct_from_candles(
        h4_candles, 21, series_cache=series_cache, timeframe=timeframe
    )
    ema50_slope = _ema_slope_pct_from_candles(
        h4_candles, 50, series_cache=series_cache, timeframe=timeframe
    )
    ema200_slope = _safe_indicator_float(h4_snap.get("ema200Slope10"))
    if ema200_slope is None:
        ema200_slope = _ema_slope_pct_from_candles(
            h4_candles, 200, series_cache=series_cache, timeframe=timeframe
        )

    out = {
        "price_vs_ema21": _price_vs_ema_label(close, ema21),
        "price_vs_ema50": _price_vs_ema_label(close, ema50),
        "price_vs_ema200": _price_vs_ema_label(close, ema200),
        "ema21_value": round(ema21, 6) if ema21 is not None else None,
        "ema50_value": round(ema50, 6) if ema50 is not None else None,
        "ema200_value": round(ema200, 6) if ema200 is not None else None,
        "ema21_slope": ema21_slope,
        "ema50_slope": ema50_slope,
        "ema200_slope": ema200_slope,
        "price_inside_ema_cluster": False,
        "ema_cluster_width_atr": None,
        "nearest_ema_resistance_distance_atr": None,
        "nearest_ema_support_distance_atr": None,
        "at_or_below_resistance": False,
        "at_or_above_support": False,
        "clean_continuation": False,
        "strong_price_contradiction": False,
        "ema_cluster_penalty_applied": False,
        "ema_cluster_penalty_reason": None,
    }

    emas = [v for v in (ema21, ema50, ema200) if v is not None]
    if len(emas) >= 2 and close is not None and atr is not None and atr > 0:
        ema_min = min(emas)
        ema_max = max(emas)
        out["price_inside_ema_cluster"] = ema_min <= close <= ema_max
        out["ema_cluster_width_atr"] = round((ema_max - ema_min) / atr, 4)
        resist = [e for e in emas if e > close]
        support = [e for e in emas if e < close]
        if resist:
            out["nearest_ema_resistance_distance_atr"] = round(
                min((e - close) / atr for e in resist), 4
            )
        else:
            out["nearest_ema_resistance_distance_atr"] = 0.0
        if support:
            out["nearest_ema_support_distance_atr"] = round(
                min((close - e) / atr for e in support), 4
            )
        else:
            out["nearest_ema_support_distance_atr"] = 0.0
        out["at_or_below_resistance"] = close <= ema_max
        out["at_or_above_support"] = close >= ema_min

    if direction == "LONG" and ema21 is not None and ema50 is not None and ema200 is not None and close is not None:
        out["clean_continuation"] = (
            close > ema21 and ema21 >= ema50 >= ema200
        )
        width = out.get("ema_cluster_width_atr")
        out["strong_price_contradiction"] = (
            close < min(ema21, ema50, ema200)
            or (
                bool(out.get("price_inside_ema_cluster"))
                and width is not None
                and width < _EMA_CLUSTER_TIGHT_ATR
            )
        )
    elif direction == "SHORT" and ema21 is not None and ema50 is not None and ema200 is not None and close is not None:
        out["clean_continuation"] = (
            close < ema21 and ema21 <= ema50 <= ema200
        )
        width = out.get("ema_cluster_width_atr")
        out["strong_price_contradiction"] = (
            close > max(ema21, ema50, ema200)
            or (
                bool(out.get("price_inside_ema_cluster"))
                and width is not None
                and width < _EMA_CLUSTER_TIGHT_ATR
            )
        )

    return out


def _adx_quality_diagnostics(
    d1_snap: dict,
    h4_snap: dict,
    asset_type: str,
    score_group: str | None,
    adx_mult: float,
    adx_val: float | None,
    adx_source: str,
) -> dict:
    """ADX gate metadata for audit panels (does not change gate behavior)."""
    trend_min, hard_fail = _resolve_adx_thresholds(asset_type, score_group)
    gate_mode = _resolve_adx_source_mode(score_group, asset_type)
    adx_slope = None
    adx_prev = None
    if adx_source == "h4":
        adx_slope = _safe_indicator_float(h4_snap.get("adxSlope"))
        adx_prev = _safe_indicator_float(h4_snap.get("adxPrev"))
    elif adx_source == "d1":
        adx_slope = _safe_indicator_float(d1_snap.get("adxSlope"))
        adx_prev = _safe_indicator_float(d1_snap.get("adxPrev"))

    adx_falling = False
    if adx_slope is not None and adx_slope < 0:
        adx_falling = True
    elif adx_val is not None and adx_prev is not None and adx_val < adx_prev:
        adx_falling = True

    return {
        "adx_threshold": round(trend_min, 4),
        "adx_hard_fail": round(hard_fail, 4),
        "adx_passed": bool(adx_mult > 0),
        "adx_gate_mode": gate_mode,
        "adx_slope": adx_slope,
        "adx_falling": adx_falling,
        "adx_timeframe_used": adx_source if adx_source in ("d1", "h4") else None,
    }


def _apply_forex_ema_cluster_penalty(
    trend_detail: dict,
    adx_quality: dict,
    direction: str,
) -> tuple[float, float | None, bool, str | None]:
    """Return (base_score_mult, final_soft_cap, applied, reason)."""
    if not bool(CONFIG.get("ENGINE_A_FOREX_EMA_CLUSTER_PENALTY_ENABLED", False)):
        return 1.0, None, False, None

    coherence = _safe_indicator_float(trend_detail.get("coherence_ratio"))
    if coherence is None:
        return 1.0, None, False, None

    min_coherence = _float_cfg(
        CONFIG.get("ENGINE_A_FOREX_EMA_CLUSTER_MIN_COHERENCE_FOR_NO_PENALTY"), 0.85
    )
    agreement_count = trend_detail.get("agreement_count")
    full_alignment = (
        agreement_count == 3
        and coherence is not None
        and abs(coherence - 1.0) < 1e-6
    )

    if coherence >= min_coherence and not full_alignment:
        return 1.0, None, False, "coherence_above_min"

    location_risk = False
    if direction == "LONG":
        location_risk = bool(trend_detail.get("at_or_below_resistance")) and not bool(
            trend_detail.get("clean_continuation")
        )
    elif direction == "SHORT":
        location_risk = bool(trend_detail.get("at_or_above_support")) and not bool(
            trend_detail.get("clean_continuation")
        )

    if full_alignment and not bool(trend_detail.get("strong_price_contradiction")):
        return 1.0, None, False, "full_alignment_clean_location"

    if not location_risk:
        return 1.0, None, False, "location_ok"

    if bool(CONFIG.get("ENGINE_A_FOREX_EMA_CLUSTER_REQUIRE_ADX_RISING_FOR_NO_PENALTY", False)):
        if adx_quality.get("adx_falling"):
            return 1.0, None, False, "adx_rising_required"

    mode = str(CONFIG.get("ENGINE_A_FOREX_EMA_CLUSTER_PENALTY_MODE", "soft_cap") or "soft_cap").strip().lower()
    if mode == "multiplier":
        mult = _float_cfg(CONFIG.get("ENGINE_A_FOREX_EMA_CLUSTER_PENALTY_MULT"), 0.85)
        mult = max(0.0, min(1.0, mult))
        return mult, None, True, "ema_cluster_multiplier"

    cap = _float_cfg(CONFIG.get("ENGINE_A_FOREX_EMA_CLUSTER_MAX_SCORE_CAP"), 2.0)
    cap = max(0.0, min(3.0, cap))
    return 1.0, cap, True, "ema_cluster_soft_cap"


def _resolve_entry_extension_params(
    cfg: dict, score_group: str | None, asset_type: str
) -> tuple[float, float, float]:
    """Resolve (near_atr, far_atr, min_mult) for the entry-extension gate."""
    near = _float_cfg(cfg.get("NEAR_ATR"), 2.0)
    far = _float_cfg(cfg.get("FAR_ATR"), 4.0)
    min_mult = _float_cfg(cfg.get("MIN_MULT"), 0.5)
    by_class = cfg.get("BY_CLASS")
    override = _resolve_class_keyed(by_class, score_group, asset_type, None)
    if isinstance(override, dict):
        near = _float_cfg(override.get("NEAR_ATR"), near)
        far = _float_cfg(override.get("FAR_ATR"), far)
        min_mult = _float_cfg(override.get("MIN_MULT"), min_mult)
    return near, far, max(0.0, min(1.0, min_mult))


def _entry_extension_multiplier(
    h4_snap: dict,
    atr: float | None,
    direction: str,
    score_group: str | None,
    asset_type: str,
) -> tuple[float, dict]:
    """Cross-asset entry-extension quality penalty (config-gated, all classes).

    Deterministic version of the AI playbook's "extended/late entry" test: how
    far the H4 close sits beyond the EMA50 anchor in ATR units, measured in the
    trade direction. Chasing entries (large positive extension) are penalised;
    pullback / at-value entries (price on the value side of EMA50, or within
    NEAR_ATR) are not. Targets the late-entry → stop-out pattern. Multiplier is
    bounded to [MIN_MULT, 1.0] — it never raises a score. Fail-closed
    (MIN_MULT) when EMA50 / ATR / close are unavailable — a chasing entry we
    can't measure must not pass unpenalised (safety: missing data fails closed).
    Set FAIL_CLOSED_ON_MISSING_DATA:false to restore prior fail-open (1.0).
    """
    cfg = CONFIG.get("ENGINE_A_ENTRY_EXTENSION_GATE") or {}
    detail = {"enabled": False, "ext_atr": None, "multiplier": 1.0, "fail_closed": False}
    if not isinstance(cfg, dict) or not bool(cfg.get("ENABLED", False)):
        return 1.0, detail
    near, far, min_mult = _resolve_entry_extension_params(cfg, score_group, asset_type)
    fail_closed = bool(cfg.get("FAIL_CLOSED_ON_MISSING_DATA", True))
    close = _safe_indicator_float(h4_snap.get("close"))
    ema50 = _safe_indicator_float(h4_snap.get("ema50"))
    a = _safe_indicator_float(atr)
    missing = close is None or ema50 is None or a is None or a <= 0 or direction not in ("LONG", "SHORT")
    if missing:
        if fail_closed:
            detail.update({
                "enabled": True,
                "multiplier": round(min_mult, 4),
                "fail_closed": True,
                "fail_reason": "missing_data",
            })
            return min_mult, detail
        return 1.0, detail
    ext_atr = (close - ema50) / a if direction == "LONG" else (ema50 - close) / a
    detail.update({"enabled": True, "ext_atr": round(ext_atr, 4)})
    if ext_atr <= near:
        return 1.0, detail
    if far <= near or ext_atr >= far:
        mult = min_mult
    else:
        t = (ext_atr - near) / (far - near)
        mult = 1.0 + t * (min_mult - 1.0)
    mult = max(min_mult, min(1.0, mult))
    detail["multiplier"] = round(mult, 4)
    return mult, detail


def _volume_provenance_uniform(candles: list | None, lookback: int) -> bool:
    """True when every bar in the lookback window shares one volume provenance.

    EODHD exchange volume and MT5 tick volume differ by ~3 orders of magnitude
    (shares traded vs price-change count), so a window straddling both makes any
    ratio or moving average meaningless — a real latest bar against a tick-volume
    mean reads as extreme volume on every signal. Uniform tick volume (no bar
    carries provenance) is the pre-overlay baseline and stays allowed.
    """
    if not isinstance(candles, list) or not candles:
        return False
    window = candles[-max(1, int(lookback)):]
    flags = {str(c.get("volSource") or "") == "eodhd" for c in window}
    return len(flags) == 1


def _h4_volume_vs_ma(h4_candles: list | None, lookback: int) -> tuple[float | None, float | None]:
    """Return (latest_volume, volume_ma) from H4 candle volume series."""
    if not isinstance(h4_candles, list) or not h4_candles:
        return None, None
    if not _volume_provenance_uniform(h4_candles, lookback):
        return None, None
    try:
        # Only the final SMA value is consumed. Building the complete expanding
        # SMA array here made historical replay O(n^2) while returning exactly
        # the same final-window mean.
        tail = h4_candles[-max(1, int(lookback)):]
        vols = [
            float(c.get("vol", 0) or c.get("volume", 0) or 0)
            for c in tail
        ]
        if not vols or vols[-1] <= 0:
            return None, None
        if len(h4_candles) < lookback:
            return vols[-1], None
        valid = [value for value in vols if value is not None]
        volume_ma = sum(valid) / len(valid) if valid else None
        if volume_ma is None or volume_ma <= 0:
            return vols[-1], None
        return vols[-1], float(volume_ma)
    except Exception:
        return None, None


def _h4_vwap_distance_metrics(
    h4_snap: dict,
    h4_candles: list | None,
    *,
    lookback: int = 96,
    series_cache=None,
    timeframe: str | None = None,
) -> dict:
    """VWAP distance metrics for crypto late-trend diagnostics (H4)."""
    out = {
        "vwap_value": None,
        "price_vs_vwap": None,
        "vwap_distance_pct": None,
        "vwap_distance_atr": None,
    }
    close = _safe_indicator_float(h4_snap.get("close"))
    atr = _safe_indicator_float(h4_snap.get("atr"))
    if close is None or close <= 0 or not isinstance(h4_candles, list) or len(h4_candles) < lookback:
        return out
    try:
        vwap = None
        if (
            series_cache is not None
            and timeframe is not None
            and series_cache.matches(timeframe, h4_candles)
        ):
            vwap = series_cache.rolling_vwap_at(
                timeframe, len(h4_candles), lookback
            )
        if vwap is None:
            vwap_candles = h4_candles[-lookback:]
            cumulative_volume = 0.0
            cumulative_typical_volume = 0.0
            for candle in vwap_candles:
                candle_close = float(candle.get("close", 0))
                typical_price = (
                    float(candle.get("high", candle_close))
                    + float(candle.get("low", candle_close))
                    + candle_close
                ) / 3.0
                volume = float(candle.get("vol", 0) or 0.0)
                if volume > 0:
                    cumulative_typical_volume += typical_price * volume
                    cumulative_volume += volume
            if cumulative_volume <= 0:
                return out
            # calc_vwap rounds every emitted point to eight decimals. Only its
            # final value was consumed here, so calculate that value directly.
            vwap = round(cumulative_typical_volume / cumulative_volume, 8)
        if vwap <= 0:
            return out
        vwap_distance_pct = (close - vwap) / vwap
        out["vwap_value"] = round(vwap, 6)
        out["vwap_distance_pct"] = round(vwap_distance_pct, 6)
        if atr is not None and atr > 0:
            out["vwap_distance_atr"] = round((close - vwap) / atr, 4)
        if close > vwap:
            out["price_vs_vwap"] = "above"
        elif close < vwap:
            out["price_vs_vwap"] = "below"
        else:
            out["price_vs_vwap"] = "at"
    except Exception as exc:
        log.debug("[EA2] crypto late-trend VWAP metrics skipped: %s", exc)
    return out


def _crypto_late_trend_diagnostics(
    h4_snap: dict,
    h4_candles: list | None,
    direction: str,
    trend_detail: dict,
    adx_quality: dict,
    adx_val: float | None,
    *,
    series_cache=None,
    timeframe: str | None = None,
) -> dict:
    """H4 late-trend continuation risk diagnostics for Engine A crypto."""
    adx_strong = _float_cfg(CONFIG.get("ENGINE_A_CRYPTO_ADX_STRONG_THRESHOLD"), 25.0)
    vwap_warn_atr = _float_cfg(CONFIG.get("ENGINE_A_CRYPTO_VWAP_EXTENSION_ATR_WARN"), 3.0)
    vol_lookback = int(CONFIG.get("ENGINE_A_CRYPTO_VOLUME_MA_LOOKBACK", 20) or 20)
    vwap_lookback = int(
        (CONFIG.get("ENGINE_A_VWAP_FILTER") or {}).get("CANDLE_LOOKBACK", 96) or 96
    )

    close = _safe_indicator_float(h4_snap.get("close"))
    ema21 = _safe_indicator_float(h4_snap.get("ema21"))
    ema50 = _safe_indicator_float(h4_snap.get("ema50"))
    adx_slope = adx_quality.get("adx_slope")
    adx_falling = bool(adx_quality.get("adx_falling"))
    adx_f = _safe_indicator_float(adx_val)

    vol_value, vol_ma = _h4_volume_vs_ma(h4_candles, vol_lookback)
    volume_below_ma = (
        vol_value is not None
        and vol_ma is not None
        and vol_value < vol_ma
    )

    vwap_metrics = _h4_vwap_distance_metrics(
        h4_snap,
        h4_candles,
        lookback=max(20, vwap_lookback),
        series_cache=series_cache,
        timeframe=timeframe,
    )
    vwap_distance_atr = _safe_indicator_float(vwap_metrics.get("vwap_distance_atr"))

    adx_below_threshold = adx_f is not None and adx_f < adx_strong
    reasons: list[str] = []
    if adx_below_threshold:
        reasons.append("adx_weak")
    if adx_falling:
        reasons.append("adx_falling")
    if volume_below_ma:
        reasons.append("volume_below_ma")

    coherence = _safe_indicator_float(trend_detail.get("coherence_ratio"))
    agreement_count = trend_detail.get("agreement_count")
    full_coherence = (
        (coherence is not None and abs(coherence - 1.0) < 1e-6)
        or agreement_count == 3
    )

    ema_stack_ok = False
    vwap_extended = False
    if direction == "LONG":
        ema_stack_ok = (
            close is not None
            and ema21 is not None
            and ema50 is not None
            and close > ema21 > ema50
        )
        if vwap_metrics.get("price_vs_vwap") == "above" and vwap_distance_atr is not None:
            if vwap_distance_atr >= vwap_warn_atr:
                vwap_extended = True
                reasons.append("vwap_extended")
    elif direction == "SHORT":
        ema_stack_ok = (
            close is not None
            and ema21 is not None
            and ema50 is not None
            and close < ema21 < ema50
        )
        if vwap_metrics.get("price_vs_vwap") == "below" and vwap_distance_atr is not None:
            if abs(vwap_distance_atr) >= vwap_warn_atr:
                vwap_extended = True
                reasons.append("vwap_extended")

    adx_weak_or_falling = adx_below_threshold or adx_falling
    # Extended mature-trend chase: penalize when structure is aligned but ADX is
    # weak/falling and price is VWAP-extended. Do not require volume_below_ma —
    # elevated volume on extended moves is often climax participation, not a
    # reason to keep full confluence (BNB/USDT audit 2026-06).
    crypto_late_trend_risk = bool(
        ema_stack_ok
        and full_coherence
        and adx_weak_or_falling
        and vwap_extended
    )

    return {
        "crypto_late_trend_risk": crypto_late_trend_risk,
        "crypto_late_trend_reason": ",".join(reasons) if reasons else None,
        "adx_value": round(adx_f, 4) if adx_f is not None else None,
        "adx_threshold": round(adx_strong, 4),
        "adx_below_threshold": adx_below_threshold,
        "adx_slope": adx_slope,
        "adx_falling": adx_falling,
        "vwap_value": vwap_metrics.get("vwap_value"),
        "price_vs_vwap": vwap_metrics.get("price_vs_vwap"),
        "vwap_distance_pct": vwap_metrics.get("vwap_distance_pct"),
        "vwap_distance_atr": vwap_metrics.get("vwap_distance_atr"),
        "vwap_extension_atr_warn": round(vwap_warn_atr, 4),
        "volume_value": round(vol_value, 4) if vol_value is not None else None,
        "volume_ma": round(vol_ma, 4) if vol_ma is not None else None,
        "volume_below_ma": volume_below_ma,
        "volume_ma_lookback": vol_lookback,
        "late_trend_adjustment_applied": False,
        "late_trend_adjustment_mult": None,
        "ema_stack_ok": ema_stack_ok,
        "full_coherence": full_coherence,
        "vwap_extended": vwap_extended,
    }


def _apply_crypto_late_trend_adjustment(
    diagnostics: dict,
    trend_detail: dict,
    direction: str,
) -> tuple[float, bool, str | None]:
    """Return (base_score_mult, applied, reason) for crypto late-trend setups."""
    if not bool(CONFIG.get("ENGINE_A_CRYPTO_LATE_TREND_ADJUSTMENT_ENABLED", True)):
        return 1.0, False, None

    if not diagnostics.get("crypto_late_trend_risk"):
        return 1.0, False, diagnostics.get("crypto_late_trend_reason") or "no_late_trend_risk"

    adx_f = _safe_indicator_float(diagnostics.get("adx_value"))
    adx_strong = _float_cfg(CONFIG.get("ENGINE_A_CRYPTO_ADX_STRONG_THRESHOLD"), 25.0)
    if (
        adx_f is not None
        and adx_f >= adx_strong
        and not bool(diagnostics.get("adx_falling"))
    ):
        return 1.0, False, "adx_strong_rising"

    vwap_warn_atr = _float_cfg(CONFIG.get("ENGINE_A_CRYPTO_VWAP_EXTENSION_ATR_WARN"), 3.0)
    vwap_distance_atr = _safe_indicator_float(diagnostics.get("vwap_distance_atr"))
    if vwap_distance_atr is None or abs(vwap_distance_atr) < vwap_warn_atr:
        return 1.0, False, "vwap_near"

    mult = _float_cfg(CONFIG.get("ENGINE_A_CRYPTO_LATE_TREND_MULT"), 0.85)
    mult = max(0.0, min(1.0, mult))
    return mult, True, "crypto_late_trend_multiplier"


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
    adx, source = _select_adx_for_gate(d1_snap, h4_snap, asset_type, score_group)
    if adx is None:
        if CONFIG.get("ADX_MISSING_BOTH_ABORT", False):
            return 0.0, None, "missing_both_abort"
        return 0.5, None, "missing"

    trend_min, hard_fail = _resolve_adx_thresholds(asset_type, score_group)
    mult = _adx_multiplier_from_value(adx, trend_min, hard_fail)
    return mult, adx, source


# ── Addon: asset-specific secondary factor ────────────────────────────────────

def _carry_addon_with_status(pair_display: str, direction: str, bar_time: Optional[str]) -> tuple[float, str]:
    """Carry z-score addon for forex pairs. Returns (_ADDON_* constant, status)."""
    try:
        from carry_feed import get_carry_z as _get_carry_z
        from carry_feed import _PAIR_CARRY_FORMULA as _CARRY_FORMULA
        if pair_display not in _CARRY_FORMULA:
            return _ADDON_NEUTRAL, "neutral"
        _as_of = bar_time[:10] if bar_time else None
        carry = _get_carry_z(pair_display, as_of_date=_as_of)
        if carry is None or carry == 0.0:
            return _ADDON_NEUTRAL, "neutral"
        carry = float(carry)
        is_long = direction == "LONG"
        if (is_long and carry > 0.5) or (not is_long and carry < -0.5):
            return _ADDON_CONFIRM, "ok"
        if (is_long and carry < -0.5) or (not is_long and carry > 0.5):
            return _ADDON_AGAINST, "ok"
        return _ADDON_NEUTRAL, "neutral"
    except Exception as exc:
        log.warning("[EA2] %s carry addon error: %s", pair_display, exc)
        return _ADDON_NEUTRAL, "error"


def _carry_addon(pair_display: str, direction: str, bar_time: Optional[str]) -> float:
    """Carry z-score addon for forex pairs. Returns _ADDON_* constant."""
    return _carry_addon_with_status(pair_display, direction, bar_time)[0]


def _cot_addon_with_status(
    pair_display: str,
    asset_type: str,
    direction: str,
    bar_time: Optional[str],
) -> tuple[float, str]:
    """COT net speculator positioning addon for commodity/forex. Returns (_ADDON_*, status)."""
    try:
        from cot_feed import get_cot_z as _get_cot_z
        from cot_feed import _PAIR_FORMULA as _COT_FORMULA
        if pair_display not in _COT_FORMULA:
            return _ADDON_NEUTRAL, "unsupported"
        _as_of = bar_time[:10] if bar_time else None
        cot = _get_cot_z(pair_display, as_of_date=_as_of)
        if cot is None or cot == 0.0:
            return _ADDON_NEUTRAL, "neutral"
        cot = float(cot)
        if abs(cot) < 1.0:
            return _ADDON_NEUTRAL, "neutral"  # insignificant signal
        is_long = direction == "LONG"
        if (is_long and cot > 0) or (not is_long and cot < 0):
            return _ADDON_CONFIRM, "ok"
        if (is_long and cot < 0) or (not is_long and cot > 0):
            return _ADDON_AGAINST, "ok"
        return _ADDON_NEUTRAL, "neutral"
    except Exception as exc:
        log.warning("[EA2] %s COT addon error: %s", pair_display, exc)
        return _ADDON_NEUTRAL, "error"


def _cot_addon(pair_display: str, asset_type: str, direction: str, bar_time: Optional[str]) -> float:
    """COT net speculator positioning addon for commodity/forex. Returns _ADDON_* constant."""
    return _cot_addon_with_status(pair_display, asset_type, direction, bar_time)[0]


def _cot_formula_supported(pair_display: str) -> bool:
    """Return whether this display has an explicit COT/proxy formula."""
    global _cot_formula_exception_warned
    try:
        from cot_feed import _PAIR_FORMULA as _COT_FORMULA
        formula = _COT_FORMULA.get(pair_display)
        return bool(formula)
    except Exception as exc:
        if not _cot_formula_exception_warned:
            _cot_formula_exception_warned = True
            log.debug("[EA2] COT formula lookup unavailable: %s", exc)
        return False


def _funding_threshold_decimal(new_key: str, old_key: str, default_decimal: float) -> float:
    """Return funding threshold as decimal rate, preferring explicit bps config."""
    if new_key in CONFIG:
        try:
            return float(CONFIG.get(new_key)) / 10000.0
        except (TypeError, ValueError):
            return default_decimal
    return float(CONFIG.get(old_key, default_decimal))


def _resolve_funding_stats(pair: dict) -> Optional[dict]:
    """Rolling mean/std of recent perp funding for z-score addon mode."""
    if not CONFIG.get("FACTOR_FUNDING_USE_ZSCORE", False):
        return None
    sym = (pair.get("symbol") or pair.get("display") or "").replace("/", "").upper()
    if not sym:
        return None
    now = time.time()
    cached = _funding_stats_cache.get(sym)
    if cached and now - cached[1] < _FUNDING_STATS_TTL:
        return cached[0]
    window = max(5, int(CONFIG.get("FACTOR_FUNDING_ZSCORE_WINDOW", 30) or 30))
    try:
        from data_feeds import _fetch_binance_funding_history, _fetch_bybit_funding_history

        end_ms = int(now * 1000)
        # ~8h funding intervals; fetch a few extra days for window stability.
        start_ms = end_ms - (window * 3 * 8 * 3600 * 1000)
        rows: list = []
        fr = _fetch_bybit_funding_history(sym, start_ms=start_ms, end_ms=end_ms, limit=200)
        if isinstance(fr, dict) and not fr.get("error"):
            rows = list(fr.get("list") or [])
        if len(rows) < 5:
            fr2 = _fetch_binance_funding_history(sym, start_ms=start_ms, end_ms=end_ms, limit=200)
            if isinstance(fr2, dict) and not fr2.get("error"):
                alt = list(fr2.get("list") or [])
                if len(alt) > len(rows):
                    rows = alt
        rates: list[float] = []
        for row in rows:
            try:
                rates.append(float(row.get("rate", 0)))
            except (TypeError, ValueError):
                continue
        if len(rates) < 5:
            return None
        sample = rates[-window:] if len(rates) > window else rates
        mean = sum(sample) / len(sample)
        var = sum((r - mean) ** 2 for r in sample) / len(sample)
        std = math.sqrt(max(0.0, var))
        min_std = float(CONFIG.get("FACTOR_FUNDING_ZSCORE_MIN_STD", 1e-6))
        if std < min_std:
            std = min_std
        stats = {"mean": mean, "std": std, "n": len(sample)}
        _funding_stats_cache[sym] = (stats, now)
        return stats
    except Exception as exc:
        log.debug("[EA2] funding stats unavailable for %s: %s", sym, exc)
        return None


def _cot_coverage_status(pair_display: str) -> str:
    """Return COT coverage label for feed_status (ok | no_coverage | unsupported)."""
    if not _cot_formula_supported(pair_display):
        return "unsupported"
    try:
        from cot_feed import get_cot_net

        detail = get_cot_net(pair_display) or {}
        return str(detail.get("_cot_coverage") or "unknown")
    except Exception:
        return "unknown"


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


def _funding_vote_continuous(
    funding_rate: Optional[float],
    direction: str,
    funding_stats: Optional[dict] = None,
) -> float:
    """Continuous funding vote in [-1,+1], sign-aligned with _funding_addon."""
    if funding_rate is None:
        return 0.0
    try:
        fr = float(funding_rate)
        if (
            CONFIG.get("FACTOR_FUNDING_USE_ZSCORE", False)
            and funding_stats
            and isinstance(funding_stats, dict)
        ):
            mean = float(funding_stats.get("mean", 0.0))
            std = float(funding_stats.get("std", 0.0))
            if std <= 1e-9:
                return 0.0
            z = (fr - mean) / std
            z_thresh = float(CONFIG.get("FACTOR_FUNDING_Z_THRESHOLD", 1.0))
            if abs(z) < z_thresh:
                return 0.0
            z_ref = max(1e-9, abs(float(CONFIG.get("FACTOR_FUNDING_Z_REF", 2.0))))
            raw = math.tanh(abs(z) / z_ref)
            funding_bullish = z < -z_thresh
        else:
            baseline = float(CONFIG.get("FACTOR_FUNDING_BASELINE", 0.0001))
            noise_band = float(CONFIG.get("FACTOR_FUNDING_NOISE_BAND", 0.0001))
            adjusted = fr - baseline
            if abs(adjusted) < noise_band:
                return 0.0
            scale = max(1e-9, abs(float(CONFIG.get("FACTOR_FUNDING_SCALE", 0.0005))))
            raw = math.tanh(abs(adjusted) / scale)
            funding_bullish = adjusted < 0
        long_vote = raw if funding_bullish else -raw
        vote = long_vote if direction == "LONG" else -long_vote
        return max(-1.0, min(1.0, float(vote)))
    except (TypeError, ValueError):
        return 0.0


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


def _oi_vote_continuous(oi_context: Optional[dict], direction: str) -> float:
    """Continuous OI divergence vote in [-1,+1], using _oi_addon's direction semantics."""
    if not oi_context:
        return 0.0
    try:
        use_smoothed = bool(CONFIG.get("FACTOR_OI_USE_SMOOTHED", True))
        oi_raw = (
            oi_context.get("oi_change_pct_smoothed")
            if use_smoothed and oi_context.get("oi_change_pct_smoothed") is not None
            else oi_context.get("oi_change_pct")
        )
        oi_chg = float(oi_raw)
        if abs(oi_chg) < 0.5:
            return 0.0
        ctx = dict(oi_context)
        ctx["oi_change_pct"] = oi_chg
        discrete = _oi_addon(ctx, direction)
        if discrete == _ADDON_CONFIRM:
            sign = 1.0
        elif discrete == _ADDON_AGAINST:
            sign = -1.0
        else:
            return 0.0
        ref = max(1e-9, abs(float(CONFIG.get("FACTOR_OI_REF_PCT", 5.0))))
        weak_mult = 0.5 if oi_chg < 0 and float(ctx["price_change_pct"]) > 0 else 1.0
        return sign * weak_mult * min(1.0, abs(oi_chg) / ref)
    except (TypeError, ValueError, KeyError):
        return 0.0


def _vote_carry(display: str, direction: str, bar_time: Optional[str]) -> tuple[float, str]:
    """Carry vote in [-1,+1]. Reuses get_carry_z; sign vs direction."""
    try:
        from carry_feed import get_carry_z as _gcz

        _as_of = (bar_time or "")[:10] or None
        z = _gcz(display, as_of_date=_as_of)
        if z is None:
            return 0.0, "neutral"
        v = max(-1.0, min(1.0, float(z) / 2.0))
        return (v if direction == "LONG" else -v), "ok"
    except Exception as exc:
        log.warning("[EA2] %s carry vote error: %s", display, exc)
        return 0.0, "error"


def _vote_cot(display: str, direction: str, bar_time: Optional[str]) -> tuple[float, str]:
    """COT speculator positioning vote in [-1,+1]."""
    try:
        from cot_feed import get_cot_z as _gcz

        _as_of = (bar_time or "")[:10] or None
        z = _gcz(display, as_of_date=_as_of)
        if z is None or z == 0.0:
            return 0.0, "neutral"
        v = max(-1.0, min(1.0, float(z) / 2.0))
        return (v if direction == "LONG" else -v), "ok"
    except Exception as exc:
        log.warning("[EA2] %s cot vote error: %s", display, exc)
        return 0.0, "error"


def _vote_funding(
    funding_rate: Optional[float],
    direction: str,
    funding_stats: Optional[dict] = None,
) -> tuple[float, str]:
    """Funding extreme vote in [-1,+1]; positive funding fades new longs."""
    if funding_rate is None:
        return 0.0, "neutral"
    try:
        return _funding_vote_continuous(funding_rate, direction, funding_stats), "ok"
    except Exception as exc:
        log.warning("[EA2] funding vote error: %s", exc)
        return 0.0, "error"


def _vote_oi_divergence(oi_context: Optional[dict], direction: str) -> tuple[float, str]:
    """OI vs price divergence vote in [-1,+1]."""
    if not oi_context:
        return 0.0, "neutral"
    try:
        v = _oi_vote_continuous(oi_context, direction)
        return v, "ok" if v != 0.0 else "neutral"
    except Exception as exc:
        log.warning("[EA2] oi vote error: %s", exc)
        return 0.0, "error"


def _vote_vol_skew(
    display: str,
    asset_type: str,
    direction: str,
    bar_time: Optional[str],
    macro_context: Optional[dict],
) -> tuple[float, str]:
    """Options/vol-term-structure vote in [-1,+1]. Neutral when feed is absent."""
    try:
        sk = macro_context.get("vol_skew") if isinstance(macro_context, dict) else None
        if sk is None:
            from vol_skew_feed import get_vol_skew_z as _gvz

            _as_of = (bar_time or "")[:10] or None
            sk = _gvz(display, as_of_date=_as_of)
        if sk is None:
            return 0.0, "neutral"
        v = -max(-1.0, min(1.0, float(sk) / 2.0))
        return (v if direction == "LONG" else -v), "ok"
    except Exception as exc:
        log.warning("[EA2] %s vol_skew vote error: %s", display, exc)
        return 0.0, "error"


def _orthogonal_vote_vector(
    pair: dict,
    direction: str,
    bar_time: Optional[str],
    funding_rate: Optional[float],
    oi_context: Optional[dict],
    funding_stats: Optional[dict],
    macro_context: Optional[dict],
) -> tuple[float, dict]:
    """Weighted independent orthogonal votes, squashed once via tanh."""
    asset_type = pair.get("type", "stock")
    display = pair.get("display", "")
    try:
        from scoring import get_pair_score_group

        score_group = get_pair_score_group(pair)
    except Exception:
        score_group = _resolve_pair_score_group(pair)
    weight_map = CONFIG.get("ENGINE_A_ORTHO_FACTOR_WEIGHTS", {}) or {}
    weights = (weight_map.get(score_group) or weight_map.get(asset_type) or {})
    k = float(CONFIG.get("ENGINE_A_ORTHO_VOTE_TANH_K", 1.0))

    votes: dict[str, dict] = {}

    def _add(name: str, fn_result: tuple[float, str], weight: float) -> None:
        v, st = fn_result
        votes[name] = {"vote": round(float(v), 4), "weight": float(weight), "status": st}

    _add("carry", _vote_carry(display, direction, bar_time), weights.get("carry", 0.0))
    _add("cot", _vote_cot(display, direction, bar_time), weights.get("cot", 0.0))
    _add(
        "funding",
        _vote_funding(funding_rate, direction, funding_stats),
        weights.get("funding", 0.0),
    )
    _add(
        "oi_divergence",
        _vote_oi_divergence(oi_context, direction),
        weights.get("oi_divergence", 0.0),
    )
    _add(
        "vol_skew",
        _vote_vol_skew(display, asset_type, direction, bar_time, macro_context),
        weights.get("vol_skew", 0.0),
    )

    weighted_sum = sum(d["vote"] * d["weight"] for d in votes.values())
    ortho_term = math.tanh(k * weighted_sum)
    detail = {
        "votes": votes,
        "weighted_sum": round(weighted_sum, 4),
        "ortho_term": round(ortho_term, 4),
        "enabled": True,
    }
    if CONFIG.get("ENGINE_A_ORTHO_VOTE_DEBUG", False):
        try:
            import json as _json
            import os as _os

            _path = CONFIG.get("ENGINE_A_ORTHO_VOTE_DEBUG_PATH", "tmp/ortho_votes.jsonl")
            _dir = _os.path.dirname(_path)
            if _dir:
                _os.makedirs(_dir, exist_ok=True)
            _rec = {
                "display": pair.get("display"),
                "score_group": score_group,
                "direction": direction,
                "bar_time": bar_time,
                "weighted_sum": detail["weighted_sum"],
                "ortho_term": detail["ortho_term"],
                "votes": {
                    k: {"vote": v["vote"], "weight": v["weight"], "status": v["status"]}
                    for k, v in detail["votes"].items()
                },
            }
            with open(_path, "a", encoding="utf-8") as _fh:
                _fh.write(_json.dumps(_rec) + "\n")
        except Exception:
            pass
    return ortho_term, detail


def _volume_price_concordance(
    h4_candles: list | None,
    direction: str,
    lookback: int = 5,
) -> float:
    """Compare volume on up-bars vs down-bars. Returns -1..+1."""
    if not h4_candles or len(h4_candles) < lookback + 1:
        return 0.0
    window = h4_candles[-(lookback + 1):]
    up_vol_sum = 0.0
    down_vol_sum = 0.0
    for i in range(1, len(window)):
        vol = float(window[i].get("vol", 0) or 0)
        if vol <= 0:
            continue
        price_up = float(window[i].get("close", 0)) > float(window[i - 1].get("close", 0))
        if price_up:
            up_vol_sum += vol
        else:
            down_vol_sum += vol

    total_vol = up_vol_sum + down_vol_sum
    if total_vol <= 0:
        return 0.0

    is_long = direction.upper() == "LONG"
    if is_long:
        return (up_vol_sum - down_vol_sum) / total_vol
    else:
        return (down_vol_sum - up_vol_sum) / total_vol


def _stock_volume_addon_with_status(
    h4_candles: list | None,
    direction: str,
    volume_ratio: float | None,
) -> tuple[float, str]:
    """Volume confirmation addon for stocks/ETFs using EODHD-overlaid H4 candles."""
    cfg = CONFIG.get("ENGINE_A_STOCK_VOLUME_ADDON") or {}
    min_vol_bars = int(cfg.get("MIN_VOLUME_BARS", 10))

    if not h4_candles or len(h4_candles) < 20:
        return _ADDON_NEUTRAL, "unsupported"

    vols = [float(c.get("vol", 0) or 0) for c in h4_candles[-20:]]
    nonzero = sum(1 for v in vols if v > 0)
    if nonzero < min_vol_bars:
        return _ADDON_NEUTRAL, "unsupported"

    vol_hi = float(cfg.get("VOL_RATIO_HIGH", 1.5))
    vol_lo = float(cfg.get("VOL_RATIO_LOW", 0.5))
    obv_lookback = int(cfg.get("OBV_LOOKBACK", 20))
    conc_lookback = int(cfg.get("CONCORDANCE_LOOKBACK", 5))

    # All three sub-signals below compare volume across bars, so the whole window
    # must come from one source. During WS warm-up (or after a gap in coverage) the
    # tail is EODHD volume over an MT5 tick-volume history; scoring that would
    # inflate every stock signal. Treat mixed windows as no volume data.
    if not _volume_provenance_uniform(
        h4_candles, max(20, obv_lookback, conc_lookback + 1)
    ):
        return _ADDON_NEUTRAL, "unsupported"

    # Sub-signal 1: volume ratio vs SMA
    latest_vol, vol_ma = _h4_volume_vs_ma(h4_candles, obv_lookback)
    vr_score = 0.0
    if latest_vol is not None and vol_ma is not None and vol_ma > 0:
        ratio = latest_vol / vol_ma
        if ratio >= vol_hi:
            vr_score = 1.0
        elif ratio <= vol_lo:
            vr_score = -1.0
        else:
            vr_score = (ratio - 1.0) / (vol_hi - 1.0) if ratio >= 1.0 else (ratio - 1.0) / (1.0 - vol_lo)

    # Sub-signal 2: OBV trend alignment
    from indicators import calc_obv_trend
    obv_trend = calc_obv_trend(h4_candles, lookback=obv_lookback)
    obv_score = 0.0
    if obv_trend == "confirming":
        obv_score = 1.0
    elif obv_trend == "diverging_bearish" and direction.upper() == "LONG":
        obv_score = -1.0
    elif obv_trend == "diverging_bullish" and direction.upper() == "SHORT":
        obv_score = -1.0

    # Sub-signal 3: volume-price concordance
    conc_score = _volume_price_concordance(h4_candles, direction, lookback=conc_lookback)

    w_vr = float(cfg.get("WEIGHT_VOL_RATIO", 0.50))
    w_obv = float(cfg.get("WEIGHT_OBV", 0.30))
    w_conc = float(cfg.get("WEIGHT_VOL_PRICE_CONCORDANCE", 0.20))
    combined = vr_score * w_vr + obv_score * w_obv + conc_score * w_conc

    if combined > 0:
        val = round(combined * _ADDON_CONFIRM, 4)
    else:
        val = round(combined * abs(_ADDON_AGAINST), 4)
    val = max(_ADDON_AGAINST, min(_ADDON_CONFIRM, val))
    return val, "ok"


def _asset_addon(
    pair: dict,
    direction: str,
    funding_rate: Optional[float],
    bar_time: Optional[str],
    oi_context: Optional[dict] = None,
    h4_candles: list | None = None,
    volume_ratio: float | None = None,
) -> tuple:
    """Resolve the single asset-class-specific addon factor.

    Returns (addon_value, addon_type, feed_status).
    """
    asset_type = pair.get("type", "stock")
    display = pair.get("display", "")

    if asset_type == "forex":
        val, status = _carry_addon_with_status(display, direction, bar_time)
        return val, "carry", status

    if asset_type == "crypto":
        funding_stats = _resolve_funding_stats(pair)
        funding_val = _funding_addon(funding_rate, direction, funding_stats=funding_stats)
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
                combo_lo = -abs(float(CONFIG.get("FACTOR_CRYPTO_ADDON_COMBO_AGAINST_CAP", -combo_hi)))
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
        if _cot_formula_supported(display):
            val, status = _cot_addon_with_status(display, asset_type, direction, bar_time)
            return val, addon_type, status
        vol_addon_cfg = CONFIG.get("ENGINE_A_STOCK_VOLUME_ADDON") or {}
        vol_addon_types = set(vol_addon_cfg.get("ASSET_TYPES", ["stock", "etf", "etf_bond", "index"]))
        if vol_addon_cfg.get("ENABLED", False) and h4_candles:
            val, status = _stock_volume_addon_with_status(h4_candles, direction, volume_ratio)
            if status != "missing":
                return val, "volume", status
            if asset_type in vol_addon_types:
                return val, "volume", status
        return _ADDON_NEUTRAL, addon_type, "unsupported"

    vol_addon_cfg = CONFIG.get("ENGINE_A_STOCK_VOLUME_ADDON") or {}
    vol_addon_types = set(vol_addon_cfg.get("ASSET_TYPES", ["stock", "etf", "etf_bond", "index"]))
    if vol_addon_cfg.get("ENABLED", False) and asset_type in vol_addon_types:
        val, status = _stock_volume_addon_with_status(h4_candles, direction, volume_ratio)
        return val, "volume", status

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
    if str(asset_type or "").lower() != "crypto":
        return 1.0, {"enabled": True, "applied": False, "reason": "crypto_only"}

    max_boost = float(cfg.get("MAX_BOOST", 0.03))
    max_penalty = float(cfg.get("MAX_PENALTY", -0.03))
    lookback = int(cfg.get("CANDLE_LOOKBACK", 96))

    if not h4_candles or len(h4_candles) < lookback:
        return 1.0, {"enabled": True, "applied": False, "reason": "insufficient_candles"}

    latest = h4_candles[-1]
    if isinstance(latest, dict) and (
        latest.get("is_final") is False
        or latest.get("isFinal") is False
        or latest.get("confirmed") is False
        or latest.get("complete") is False
    ):
        return 1.0, {"enabled": True, "applied": False, "reason": "unfinalized_bar"}

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
    variance = sum((x - mean) ** 2 for x in window) / len(window)
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
    """Forex session liquidity multiplier — enabled by default for forex.

    Uses UTC hour buckets from ``scoring.get_session`` (same labels as scan UI).
    Disabled for non-forex assets (returns 1.0).
    """
    if str(asset_type or "").lower() != "forex":
        return 1.0
    cfg = CONFIG.get("FACTOR_FOREX_SESSION_MULT") or {}
    if not bool(cfg.get("ENABLED", True)):
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
    style: str | None = None,
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
    scoring_profile = resolve_engine_a_scoring_profile(
        score_group=score_group,
        asset_type=asset_type,
        style=style,
    )
    _regime_snap = snap_for_tf(
        scoring_profile,
        d1_snap=d1_snap,
        h4_snap=h4_snap,
        h1_snap=h1_snap,
        tf=scoring_profile.get("regime_tf", "H4"),
    )
    _mom_snap = snap_for_tf(
        scoring_profile,
        d1_snap=d1_snap,
        h4_snap=h4_snap,
        h1_snap=h1_snap,
        tf=scoring_profile.get("momentum_tf", "H4"),
    )

    # ── Data quality guard ────────────────────────────────────────────────────
    _close = h4_snap.get("close")
    _atr = h4_snap.get("atr")
    try:
        _atr_float = float(_atr) if _atr is not None else None
    except (TypeError, ValueError):
        _atr_float = None
    if _close and (_atr_float is None or not math.isfinite(_atr_float) or _atr_float <= 0):
        log.warning("[EA2] %s ATR=0 — frozen candle data suspected", display)
        feed_status["atr"] = "invalid"
        regime_raw = detect_regime(_regime_snap, asset_type).get("regime", "UNKNOWN")
        regime = _get_smoothed_regime(regime_context, pair_id, regime_raw)
        return _zero_result(pair, regime, {"error": "atr_invalid"}, feed_status,
                            reason="atr_invalid_abort", direction=None)

    # ── FACTOR 1: Trend ───────────────────────────────────────────────────────
    d1_prev = _previous_indicator_snap(d1_candles, pair_id=pair_id, tf="D1", score_group=score_group, asset_type=asset_type)
    h4_prev = _previous_indicator_snap(h4_candles, pair_id=pair_id, tf="H4", score_group=score_group, asset_type=asset_type)
    h1_prev = _previous_indicator_snap(h1_candles, pair_id=pair_id, tf="H1", score_group=score_group, asset_type=asset_type)
    trend_score, direction, trend_detail = _coherent_trend_score(
        d1_snap, h4_snap, h1_snap, asset_type,
        d1_prev=d1_prev,
        h4_prev=h4_prev,
        h1_prev=h1_prev,
        score_group=score_group,
        scoring_profile=scoring_profile,
    )
    trend_detail["hysteresis_prev_available"] = {
        "d1": d1_prev is not None,
        "h4": h4_prev is not None,
        "h1": h1_prev is not None,
    }

    # Hard abort: no direction determinable
    # direction=None from _coherent_trend_score means D1/H4/H1 trend votes are
    # perfectly balanced (weighted tie) or no EMA data was available.  This is an
    # intentional hard abort — the engine genuinely cannot determine direction.
    _reversal_pending_only = (
        bool(trend_detail.get("reversal_pending"))
        and trend_detail.get("direction_status") == "reversal_pending"
        and direction in ("LONG", "SHORT")
    )
    if _reversal_pending_only:
        log.debug("[EA2] %s reversal-pending advisory direction=%s", display, direction)
        _bbw_pct_for_exit = h4_snap.get("bbWidth_pct") or h4_snap.get("bb_width_pct")
        regime_raw = detect_regime(_regime_snap, asset_type, bb_width_pct=_bbw_pct_for_exit).get("regime", "UNKNOWN")
        regime = _get_smoothed_regime(regime_context, pair_id, regime_raw)
        return _reversal_pending_advisory_result(
            pair, regime, trend_detail, feed_status, direction=direction,
        )

    if direction is None or abs(trend_score) < 1e-9:
        log.debug("[EA2] %s trend indeterminate — score=0", display)
        _bbw_pct_for_exit = h4_snap.get("bbWidth_pct") or h4_snap.get("bb_width_pct")
        regime_raw = detect_regime(_regime_snap, asset_type, bb_width_pct=_bbw_pct_for_exit).get("regime", "UNKNOWN")
        regime = _get_smoothed_regime(regime_context, pair_id, regime_raw)
        _trend_err = str(trend_detail.get("error") or "")
        _reason = (
            _trend_err
            if _trend_err in _NON_TRADABLE_ABORT_REASONS
            else "indeterminate_trend"
        )
        return _zero_result(pair, regime, trend_detail, feed_status, reason=_reason)

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
        regime_raw = detect_regime(_regime_snap, asset_type).get("regime", "UNKNOWN")
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
    adx_quality = _adx_quality_diagnostics(
        d1_snap, h4_snap, asset_type, score_group, adx_mult, adx_val, adx_source
    )
    ema_cluster_penalty_mult = 1.0
    ema_cluster_soft_cap = None

    if asset_type == "forex" and bool(
        CONFIG.get("ENGINE_A_FOREX_EMA_CLUSTER_DIAGNOSTICS_ENABLED", True)
    ):
        trend_detail.update(_forex_ema_cluster_diagnostics(h4_snap, h4_candles, direction))

    # Hard abort: dead market or fail-closed missing ADX data.
    if adx_mult == 0.0:
        if adx_source == "missing_both_abort":
            abort_reason = "adx_missing_both_abort"
            log.debug("[EA2] %s ADX unavailable on D1/H4 — fail-closed abort", display)
        else:
            abort_reason = "adx_hard_abort"
            log.debug("[EA2] %s ADX=%.1f hard abort — dead market", display, adx_val or 0)
        regime_raw = detect_regime(_regime_snap, asset_type).get("regime", "UNKNOWN")
        regime = _get_smoothed_regime(regime_context, pair_id, regime_raw)
        return _zero_result(pair, regime, trend_detail, feed_status, reason=abort_reason,
                            adx_val=adx_val, direction=direction)

    # ── FACTOR 2: Momentum quality ────────────────────────────────────────────
    mom_quality = _momentum_quality(
        _mom_snap,
        direction,
        asset_type,
        score_group=score_group,
        h4_candles=h4_candles,
    )

    # Apply Stochastic RSI modifier (experimental, config-gated)
    stoch_rsi_adj = _stochastic_rsi_modifier(h4_candles, direction, asset_type)
    mom_quality = max(-1.0, min(1.0, mom_quality + stoch_rsi_adj))
    if stoch_rsi_adj != 0.0:
        feed_status["stoch_rsi"] = f"{stoch_rsi_adj:.2f}"

    # ── ADDON: Asset-specific secondary factor ────────────────────────────────
    addon_val, addon_type, addon_status = _asset_addon(
        pair, direction, funding_rate, bar_time, oi_context=oi_context,
        h4_candles=h4_candles, volume_ratio=volume_ratio,
    )
    addon_val_before_research = addon_val
    feed_status["addon"] = f"{addon_type}:{addon_status}"
    if addon_type in ("cot", "cot_proxy"):
        feed_status["cot_coverage"] = _cot_coverage_status(display)
    if asset_type == "stock":
        if bool(CONFIG.get("INSIDER_TRADING_DIAGNOSTIC_ENABLED", False)):
            feed_status["insider_trading"] = "advisory_only"
        if bool(CONFIG.get("FUNDAMENTALS_DIAGNOSTIC_ENABLED", False)):
            feed_status["fundamentals"] = "advisory_only"
    _ortho_enabled = bool(CONFIG.get("ENGINE_A_ORTHO_VOTE_ENABLED", False))

    research_val, research_detail = _research_lab_candidate_addon(
        pair, direction, h4_candles, d1_candles, asset_type, score_group,
        h4_snap=h4_snap, d1_snap=d1_snap
    )
    if research_detail.get("enabled"):
        addon_val += research_val
        feed_status["research_lab"] = f"{research_val:.2f}"

    # Stage 3.6: Cross-engine research lab correlation cap.
    # If Engine B research lab is also active for this pair, the two labs may
    # double-count the same price-action evidence. Cap total research contribution
    # so it cannot exceed the standalone research bonus by more than 50%.
    _engine_b_rl_cfg = CONFIG.get("ENGINE_B_RESEARCH_LAB_FACTORS") or {}
    _engine_b_groups = _engine_b_rl_cfg.get("GROUPS") if isinstance(_engine_b_rl_cfg, dict) else {}
    _engine_b_rl_enabled = (
        bool(_engine_b_rl_cfg.get("ENABLED", False))
        and isinstance(_engine_b_groups, dict)
        and bool(_engine_b_groups.get(score_group))
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
    # Check exceeds-single-cap AFTER research lab is added so the wider combo
    # cap applies when the post-research total exceeds the single-signal band.
    _asset_addon_exceeds_single_cap = (
        not _ortho_enabled
        and
        asset_type == "crypto"
        and (addon_val > _ADDON_CONFIRM or addon_val < _ADDON_AGAINST)
    )
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
    if not _ortho_enabled:
        addon_val = max(_addon_lo, min(_addon_hi, addon_val))
    addon_val_without_positive_research = max(
        _addon_lo,
        min(_addon_hi, addon_val_before_research),
    )

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
    else:
        feed_status["mean_reversion"] = "disabled"

    # ── Volatility scaler (Stage 3.4) ─────────────────────────────────────────
    _close_for_vol = h4_snap.get("close")
    try:
        _close_for_vol = float(_close_for_vol) if _close_for_vol is not None else None
    except (TypeError, ValueError):
        _close_for_vol = None
    vol_scaler = _volatility_scaler(
        _atr if _atr else 0.0, _close_for_vol, asset_type, score_group
    )

    # Structurally volatile subgroups use score_group-specific ATR% bands when
    # configured; otherwise they fall back to their asset-type bands. No extra
    # clamp is applied here.

    feed_status["vol_scaler"] = f"{vol_scaler:.2f}"

    # ── Session multiplier (forex only, config-gated) ────────────────────────
    session_mult = _session_multiplier(bar_time, asset_type)
    _session_cfg = CONFIG.get("FACTOR_FOREX_SESSION_MULT") or {}
    if asset_type == "forex" and not bool(_session_cfg.get("ENABLED", True)):
        feed_status["session"] = "disabled"
    else:
        feed_status["session"] = f"{session_mult:.2f}"

    # Optional equity-session weighting is isolated from the existing forex
    # session multiplier and is config-gated; runtime YAML currently enables a
    # small +/-2% stock/index/ETF liquidity nudge.
    equity_session_mult, equity_session_detail = _equity_session_multiplier(bar_time, asset_type)
    if equity_session_detail.get("enabled"):
        feed_status["equity_session"] = str(equity_session_detail.get("session") or equity_session_detail.get("reason") or "neutral")

    # ── +DI/-DI directional alignment multiplier ──────────────────────────────
    # Stage 1.3: ADX measures strength but not direction. If EMA says LONG but
    # -DI > +DI, bearish pressure dominates — score must be suppressed.
    _di_mults = _resolve_di_alignment_multipliers(score_group, asset_type)
    _di_gap_balanced, _di_gap_opposed = _resolve_di_alignment_gap_thresholds()
    _di_gap_span = max(1e-9, _di_gap_opposed - _di_gap_balanced)

    def _di_alignment_multiplier(trend_dir: str, plus_di: float | None, minus_di: float | None) -> float:
        if plus_di is None or minus_di is None:
            return _di_mults["missing"]
        di_diff = plus_di - minus_di
        if trend_dir == "LONG":
            if plus_di > minus_di:
                return 1.0
            elif abs(di_diff) <= _di_gap_balanced:
                t = abs(di_diff) / _di_gap_balanced
                return 1.0 + t * (_di_mults["balanced"] - 1.0)
            elif abs(di_diff) <= _di_gap_opposed:
                t = (abs(di_diff) - _di_gap_balanced) / _di_gap_span
                return _di_mults["balanced"] + t * (_di_mults["opposed"] - _di_mults["balanced"])
            else:
                return _di_mults["severe_opposed"]
        elif trend_dir == "SHORT":
            if minus_di > plus_di:
                return 1.0
            elif abs(di_diff) <= _di_gap_balanced:
                t = abs(di_diff) / _di_gap_balanced
                return 1.0 + t * (_di_mults["balanced"] - 1.0)
            elif abs(di_diff) <= _di_gap_opposed:
                t = (abs(di_diff) - _di_gap_balanced) / _di_gap_span
                return _di_mults["balanced"] + t * (_di_mults["opposed"] - _di_mults["balanced"])
            else:
                return _di_mults["severe_opposed"]
        return _di_mults["missing"]

    _plus_di = h4_snap.get("plusDI") or h4_snap.get("plus_di")
    _minus_di = h4_snap.get("minusDI") or h4_snap.get("minus_di")
    di_align_mult = _di_alignment_multiplier(direction, _plus_di, _minus_di)
    feed_status["di_align"] = f"{di_align_mult:.2f}"
    if di_align_mult <= _di_mults.get("severe_opposed", 0.35) and _plus_di is not None and _minus_di is not None:
        feed_status["di_align_opposed"] = "true"
        log.debug(
            "[EA2] %s DI opposed to trend direction=%s plusDI=%s minusDI=%s mult=%.2f",
            display,
            direction,
            _plus_di,
            _minus_di,
            di_align_mult,
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
    ortho_detail = None
    _ortho_w = 0.0

    # When addon is unsupported (stock/index), redistribute addon weight.
    # ADDON_UNSUPPORTED_SPLIT (default 0.5): fraction of addon weight that goes
    # to base (raises floor) vs momentum (amplifies single-factor).  At 0.5 the
    # split is 50/50 so momentum carries 0.65 instead of 0.80.
    _eff_mom_w = _momentum_w
    _eff_addon_w = _addon_w
    _eff_base_w = _base_w
    if (not _ortho_enabled) and addon_status == "unsupported":
        _split_to_base = _resolve_addon_split(score_group, asset_type)
        _eff_base_w = _base_w + _addon_w * _split_to_base
        _eff_mom_w = _momentum_w + _addon_w * (1.0 - _split_to_base)
        _eff_addon_w = 0.0

    if _ortho_enabled:
        _ortho_w_by_class = CONFIG.get("ENGINE_A_ORTHO_CONVICTION_WEIGHT_BY_CLASS", {}) or {}
        _ortho_w = float(_ortho_w_by_class.get(asset_type, 0.20))
        ortho_term, ortho_detail = _orthogonal_vote_vector(
            pair,
            direction,
            bar_time,
            funding_rate,
            oi_context,
            funding_stats=_resolve_funding_stats(pair) if asset_type == "crypto" else None,
            macro_context=macro_context,
        )
        _eff_addon_w = 0.0
        conviction = (
            _eff_base_w
            + _eff_mom_w * mom_quality
            + _ortho_w * ((ortho_term + 1.0) / 2.0)
        )
    else:
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
    regime_raw = detect_regime(_regime_snap, asset_type, bb_width_pct=_bbw_pct).get("regime", "UNKNOWN")
    regime = _get_smoothed_regime(regime_context, pair_id, regime_raw)

    # ── Final score ───────────────────────────────────────────────────────────
    # base_score: driven by trend coherence (0-3.0 scale from _coherent_trend_score)
    # applied: adx_mult * session_mult * di_align_mult * conviction blend
    # Formula: abs(trend_score) * adx_mult * session_mult * di_align_mult *
    #          (floor + (1-floor)*conviction)
    #
    # The conviction floor is regime-conditional when CONVICTION_FLOOR_BY_REGIME is
    # present. RANGING / HIGH_VOLATILITY use a lower floor (default 0.10) because
    # momentum noise is higher — weak momentum should count for less.
    _floor_by_regime = CONFIG.get("CONVICTION_FLOOR_BY_REGIME") or {}
    if isinstance(_floor_by_regime, dict) and regime in _floor_by_regime:
        _eff_floor = float(_floor_by_regime[regime])
    elif regime in {"RANGING", "HIGH_VOLATILITY"}:
        _eff_floor = 0.10
    else:
        _eff_floor = _conviction_floor
    # An explicit ENGINE_A_CONVICTION_FLOOR_BY_CLASS entry replaces the
    # regime-conditional floor above. _apply_regime_floor_sensitivity then re-applies
    # the RANGING / HIGH_VOLATILITY reduction for classes listed in
    # ENGINE_A_CONVICTION_FLOOR_REGIME_SENSITIVE_CLASSES (stock/index/commodity/crypto),
    # so their weak momentum counts less in noisy regimes; forex is omitted and keeps
    # its strict flat floor for score reachability.
    _regime_floor = 0.10
    if isinstance(_floor_by_regime, dict) and regime in _floor_by_regime:
        _regime_floor = float(_floor_by_regime[regime])
    _eff_floor = _resolve_conviction_floor(score_group, asset_type, _eff_floor)
    _eff_floor = _apply_regime_floor_sensitivity(
        _eff_floor, regime, score_group, asset_type, _regime_floor
    )

    vol_regime_mult, vol_regime_detail = _volatility_regime_multiplier(
        regime, asset_type, score_group
    )
    if vol_regime_detail.get("enabled"):
        feed_status["vol_regime"] = f"{vol_regime_detail.get('multiplier', 1.0):.2f}"

    # FIX 3: Recalibrate Cost/Funding Penalty Sensitivity
    # Computed before base_score so the cost factor participates in the
    # multiplier chain (boosts are no longer wasted near the 3.0 cap).
    _cost_penalty = 0.0
    _fund_high = _funding_threshold_decimal("FACTOR_FUNDING_HIGH_BPS_8H", "FACTOR_FUNDING_HIGH_PCT", 0.001)
    _fund_low = _funding_threshold_decimal("FACTOR_FUNDING_LOW_BPS_8H", "FACTOR_FUNDING_LOW_PCT", -0.001)
    _fund_pen_cap = float(CONFIG.get("FACTOR_FUNDING_PENALTY_CAP", 0.10))
    _fund_pen_mult = float(CONFIG.get("FACTOR_FUNDING_PENALTY_MULT", 5.0))
    _fund_bonus_cap = float(CONFIG.get("FACTOR_FUNDING_BONUS_CAP", 0.05))
    _fund_bonus_mult = float(CONFIG.get("FACTOR_FUNDING_BONUS_MULT", 2.5))
    # NOTE: forex carry cost penalty removed (2026-06-07) -- carry direction is
    # already captured by _carry_addon_with_status() via addon_val/conviction.
    # Double-applying it via base_score multiplier overstated both reward and penalty.
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

    # ── Entry-extension quality gate (cross-asset, config-gated) ──────────────
    # Penalise entries that chase price far from the EMA50 anchor in ATR units;
    # pullback / at-value entries are unpenalised. Never raises score.
    entry_ext_mult, entry_ext_detail = _entry_extension_multiplier(
        h4_snap, _atr, direction, score_group, asset_type
    )
    if entry_ext_detail.get("enabled"):
        feed_status["entry_ext"] = f"{entry_ext_mult:.2f}"
        if entry_ext_mult < 1.0:
            feed_status["entry_extended"] = str(entry_ext_detail.get("ext_atr"))

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
        * entry_ext_mult
        * (1.0 - _cost_penalty)
    )

    crypto_late_trend = None
    _late_trend_mult = 1.0
    if asset_type == "forex":
        (
            ema_cluster_penalty_mult,
            ema_cluster_soft_cap,
            _ema_pen_applied,
            _ema_pen_reason,
        ) = _apply_forex_ema_cluster_penalty(trend_detail, adx_quality, direction)
        if _ema_pen_applied:
            trend_detail["ema_cluster_penalty_applied"] = True
            trend_detail["ema_cluster_penalty_reason"] = _ema_pen_reason
            feed_status["ema_cluster_penalty"] = _ema_pen_reason or "applied"
        base_score *= ema_cluster_penalty_mult

    if asset_type == "crypto" and bool(
        CONFIG.get("ENGINE_A_CRYPTO_LATE_TREND_DIAGNOSTICS_ENABLED", True)
    ):
        crypto_late_trend = _crypto_late_trend_diagnostics(
            h4_snap,
            h4_candles,
            direction,
            trend_detail,
            adx_quality,
            adx_val,
        )
        _lt_mult, _lt_applied, _lt_reason = _apply_crypto_late_trend_adjustment(
            crypto_late_trend,
            trend_detail,
            direction,
        )
        if _lt_applied:
            base_score *= _lt_mult
            _late_trend_mult = _lt_mult
            crypto_late_trend["late_trend_adjustment_applied"] = True
            crypto_late_trend["late_trend_adjustment_mult"] = round(_lt_mult, 4)
            feed_status["crypto_late_trend"] = _lt_reason or "applied"

    # ── Correlated penalty drawdown guard (config-gated, default off) ─────────
    # Mirror of ENGINE_A_CORRELATED_OVERLAY_GUARD on the downside: the
    # trend-quality penalties (DI alignment, VWAP, volatility regime, forex
    # EMA cluster, crypto late trend) frequently fire on the same underlying
    # evidence (weak short-term momentum inside a valid trend).
    # The product is always reported for telemetry; when ENABLED, the combined
    # multiplier is floored at MIN_COMBINED_MULT so stacked correlated
    # penalties cannot compound below it. The directional ramp is excluded: it
    # is the soft half of the min-directional gate and must stay authoritative.
    # ADX gate, vol scaler, session, and cost multipliers are intentionally
    # out of scope (independent evidence).
    _corr_penalty_product = (
        di_align_mult
        * vwap_mult
        * vol_regime_mult
        * ema_cluster_penalty_mult
        * _late_trend_mult
    )
    _corr_pen_cfg = CONFIG.get("ENGINE_A_CORRELATED_PENALTY_GUARD") or {}
    if not isinstance(_corr_pen_cfg, dict):
        _corr_pen_cfg = {}
    _corr_pen_enabled = bool(_corr_pen_cfg.get("ENABLED", False))
    _corr_pen_floor = _float_cfg(_corr_pen_cfg.get("MIN_COMBINED_MULT"), 0.50)
    _corr_pen_applied = False
    if (
        _corr_pen_enabled
        and 0.0 < _corr_penalty_product < _corr_pen_floor
        and base_score > 0
    ):
        base_score *= _corr_pen_floor / _corr_penalty_product
        _corr_pen_applied = True
        feed_status["correlated_penalty_guard"] = "floored"

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
        else:
            _macro_score = macro_context.get("usd_proxy_score") if macro_context.get("usd_proxy_score") is not None else macro_context.get("score")
            if isinstance(_macro_score, (int, float)):
                _dir_sign = 1 if direction == "LONG" else -1
                _normalized_score = max(-1.0, min(1.0, _macro_score / 3.0))
                _macro_adj = _normalized_score * _dir_sign * _macro_adj_max
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
        _has_rich_intermarket_context = any(
            key in intermarket_context for key in ("drivers", "unavailablePriors")
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
    final_score = final_score * (1.0 + _total_adj)
    # Apply mean reversion adjustment (additive, bounded)
    final_score = final_score + mean_rev_adj
    if not math.isfinite(float(final_score)):
        feed_status["final_score"] = "invalid"
        return _zero_result(pair, regime, {"error": "final_score_invalid"}, feed_status,
                            reason="final_score_invalid_abort", direction=direction)
    final_score = max(0.0, min(3.0, final_score))
    if ema_cluster_soft_cap is not None:
        final_score = min(final_score, ema_cluster_soft_cap)

    # ── Multiplier attribution telemetry (report-only, always on) ─────────────
    # For each multiplier in the chain, record its value and the counterfactual
    # score with that multiplier alone removed (set to 1.0). Computed before
    # intermarket/structure overlays; mean_rev is additive so it is excluded
    # from the division. scoring.py annotates threshold-flip flags downstream.
    _conv_blend_mult = _eff_floor + (1.0 - _eff_floor) * conviction
    _attribution_inputs = {
        "adx": adx_mult,
        "vol_scaler": vol_scaler,
        "session": session_mult,
        "equity_session": equity_session_mult,
        "vol_regime": vol_regime_mult,
        "di_align": di_align_mult,
        "dir_ramp": dir_ramp_mult,
        "vwap": vwap_mult,
        "entry_ext": entry_ext_mult,
        "cost": 1.0 - _cost_penalty,
        "ema_cluster": ema_cluster_penalty_mult,
        "crypto_late_trend": _late_trend_mult,
        "conviction_blend": _conv_blend_mult,
        "total_adj": 1.0 + _total_adj,
    }
    _mult_product_part = base_score * _conv_blend_mult * (1.0 + _total_adj)
    multiplier_attribution = {}
    for _attr_name, _attr_mult in _attribution_inputs.items():
        try:
            _attr_mult = float(_attr_mult)
        except (TypeError, ValueError):
            continue
        if abs(_attr_mult - 1.0) < 1e-9 or _attr_mult <= 0:
            continue
        _score_without = max(
            0.0, min(3.0, _mult_product_part / _attr_mult + mean_rev_adj)
        )
        multiplier_attribution[_attr_name] = {
            "multiplier": round(_attr_mult, 4),
            "score_without": round(_score_without, 4),
        }

    research_score_uplift_for_guard = 0.0
    if (not _ortho_enabled) and research_detail.get("enabled") and research_val > 0:
        addon_norm_without_positive_research = (
            addon_val_without_positive_research / _ADDON_CONFIRM
            if _ADDON_CONFIRM > 0
            else 0.0
        )
        conviction_without_positive_research = (
            _eff_base_w
            + _eff_mom_w * mom_quality
            + _eff_addon_w * addon_norm_without_positive_research
        )
        conviction_without_positive_research = max(
            0.0,
            min(1.0, conviction_without_positive_research),
        )
        score_without_positive_research = base_score * (
            _eff_floor + (1.0 - _eff_floor) * conviction_without_positive_research
        )
        score_without_positive_research = score_without_positive_research * (
            1.0 + _total_adj
        )
        score_without_positive_research = max(
            0.0,
            min(3.0, score_without_positive_research + mean_rev_adj),
        )
        research_score_uplift_for_guard = max(
            0.0,
            final_score - score_without_positive_research,
        )

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
            log.warning("[EA2] %s intermarket confirmation skipped: %s", display, exc)
            feed_status["intermarket"] = "error"

    structure_adjustment = {
        "enabled": bool(CONFIG.get("ENGINE_A_STRUCTURE_CONTEXT_ENABLED", False)),
        "applied": False,
        "adjusted_score": round(final_score, 6),
    }
    correlated_overlay_guard = {
        "enabled": bool(CONFIG.get("ENGINE_A_CORRELATED_OVERLAY_GUARD_ENABLED", True)),
        "warning": False,
        "capped": False,
        "adjusted_score": round(final_score, 6),
    }
    if structure_adjustment["enabled"] and isinstance(structure_result, dict):
        try:
            from athena_app.services.structure_context import (
                apply_engine_a_correlated_overlay_guard,
                apply_structure_context_to_score,
            )

            _pre_structure_score = float(final_score or 0.0)
            structure_adjustment = apply_structure_context_to_score(
                structure_result,
                direction=direction,
                base_score=_pre_structure_score,
                max_score=3.0,
            )
            adjusted = structure_adjustment.get("adjusted_score")
            if isinstance(adjusted, (int, float)):
                final_score = max(0.0, min(3.0, float(adjusted)))
            correlated_overlay_guard = apply_engine_a_correlated_overlay_guard(
                base_score_before_structure=_pre_structure_score,
                adjusted_score=final_score,
                research_lab_value=research_val,
                research_lab_detail=research_detail,
                research_score_uplift=research_score_uplift_for_guard,
                structure_adjustment=structure_adjustment,
                max_score=3.0,
            )
            _guard_adjusted = correlated_overlay_guard.get("adjusted_score")
            if isinstance(_guard_adjusted, (int, float)):
                final_score = max(0.0, min(3.0, float(_guard_adjusted)))
                structure_adjustment = dict(structure_adjustment)
                structure_adjustment["adjusted_score"] = round(final_score, 6)
                structure_adjustment["correlated_overlay_guard"] = correlated_overlay_guard
            if correlated_overlay_guard.get("warning"):
                feed_status["correlated_overlay_guard"] = (
                    "capped" if correlated_overlay_guard.get("capped") else "warning"
                )
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
            correlated_overlay_guard = {
                "enabled": bool(CONFIG.get("ENGINE_A_CORRELATED_OVERLAY_GUARD_ENABLED", True)),
                "warning": False,
                "capped": False,
                "adjusted_score": round(final_score, 6),
                "error": "structure_context_error",
            }

    from athena_app.services.engine_a_direction import apply_direction_conflict_to_result

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
    if ortho_detail is not None:
        factor_scores["ortho"] = {
            name: d["vote"] for name, d in ortho_detail["votes"].items()
        }
        factor_scores["ortho_term"] = ortho_detail["ortho_term"]

    weights_payload = {
        "trend": 1.0,
        "momentum": _eff_mom_w,
        "addon": _eff_addon_w,
        "base": _eff_base_w,
    }
    if _ortho_enabled:
        weights_payload["ortho"] = _ortho_w

    log.debug(
        "[EA2] %s dir=%s score=%.3f trend=%.3f adx=%.1f(%s) mom=%.3f addon=%.3f(%s) mean_rev=%.3f sess=%.2f regime=%s",
        display, direction, final_score, trend_score, adx_val or 0, adx_source,
        mom_quality, addon_val, addon_type, mean_rev_adj, session_mult, regime,
    )

    _tc = trend_detail or {}
    result = {
        # ── Core outputs ──────────────────────────────────────────────────────
        "final_score": round(final_score, 4),
        "direction": direction,
        "regime": regime,
        "signal_status": "ok",
        "signalExecutable": True,
        "directionStatus": _tc.get("direction_status") or "ok",
        "reversalPending": bool(_tc.get("reversal_pending")),
        "reversalPendingDirection": _tc.get("reversal_pending_direction"),
        # ── Factor breakdown (UI + AI) ────────────────────────────────────────
        "factor_scores": factor_scores,
        "factor_breakdown": {
            "trend": round(trend_score, 4),
            "momentum": round(mom_quality, 4),
            "addon": round(addon_val, 4),
            "research_lab": round(research_val, 4),
            "mean_reversion": round(mean_rev_adj, 4),
            "adx_multiplier": round(adx_mult, 4),
            "conviction": round(conviction, 4),
            "final_score": round(final_score, 4),
        },
        "weights": weights_payload,
        "asset_type": asset_type,
        "score_group": score_group,
        "scoring_profile": scoring_profile_public_dict(scoring_profile),
        "scoring_style": scoring_profile.get("style"),
        "momentum_timeframe": scoring_profile.get("momentum_tf"),
        "regime_timeframe": scoring_profile.get("regime_tf"),
        "engine_a_asset_diagnostics": asset_diagnostics,
        "structure_context_adjustment": structure_adjustment,
        "engine_a_correlated_overlay_guard": correlated_overlay_guard,
        "trend_coherence": trend_detail,
        # ── Diagnostic fields (backward compat with Engine C / scoring.py) ───
        "directional_score": round(trend_score, 4),
        "nondirectional_score": round(mom_quality, 4),
        "unweighted_directional_sum": round(trend_score, 4),
        "adx_value": adx_val,
        "adx_source": adx_source,
        "adx_multiplier": adx_mult,
        "adx_threshold": adx_quality.get("adx_threshold"),
        "adx_passed": adx_quality.get("adx_passed"),
        "adx_gate_mode": adx_quality.get("adx_gate_mode"),
        "adx_slope": adx_quality.get("adx_slope"),
        "adx_falling": adx_quality.get("adx_falling"),
        "adx_timeframe_used": adx_quality.get("adx_timeframe_used"),
        "momentum_quality": round(mom_quality, 4),
        "addon_type": addon_type,
        "addon_value": round(addon_val, 4),
        "orthoVote": ortho_detail,
        "orthoEnabled": _ortho_enabled,
        "research_lab_value": round(research_val, 4),
        "research_lab_detail": research_detail,
        "research_lab_score_uplift": round(research_score_uplift_for_guard, 6),
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
        "crypto_engine_a_diagnostics": crypto_late_trend,
        "multiplier_attribution": multiplier_attribution,
        "correlated_penalty_guard": {
            "enabled": _corr_pen_enabled,
            "product": round(_corr_penalty_product, 4),
            "floor": round(_corr_pen_floor, 4),
            "applied": _corr_pen_applied,
        },
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
    return apply_direction_conflict_to_result(
        result,
        intermarket_confirmation=intermarket_confirmation,
    )


_NON_TRADABLE_ABORT_REASONS = {
    "atr_invalid_abort",
    "indeterminate_trend",
    "min_directional_failed",
    "adx_hard_abort",
    "adx_missing_both_abort",
    "final_score_invalid",
    "weighted_tf_tie",
    "direction_margin_below_min",
}


def _reversal_pending_advisory_result(
    pair: dict,
    regime: str,
    trend_detail: dict,
    feed_status: dict,
    *,
    direction: str,
) -> dict:
    """Advisory-only reversal direction before 2-bar EMA confirmation."""
    asset_type = pair.get("type", "stock")
    score_group = _resolve_pair_score_group(pair)
    weight_cfg = _resolve_factor_weights(score_group, asset_type)
    pending_dir = str(direction or "").upper()
    return {
        "final_score": 0.0,
        "direction": pending_dir if pending_dir in ("LONG", "SHORT") else None,
        "diagnostic_direction": pending_dir,
        "signalExecutable": False,
        "directionStatus": "reversal_pending",
        "reversalPending": True,
        "reversalPendingDirection": trend_detail.get("reversal_pending_direction"),
        "directionConflicted": False,
        "directionConflictedWithIntermarket": False,
        "directionConflictedWithBtc": False,
        "regime": regime,
        "signal_status": "advisory",
        "factor_scores": {"trend": 0.0, "momentum": 0.0, "addon": 0.0, "research_lab": 0.0, "mean_reversion": 0.0},
        "factor_breakdown": {
            "trend": 0.0,
            "momentum": 0.0,
            "addon": 0.0,
            "research_lab": 0.0,
            "mean_reversion": 0.0,
            "adx_multiplier": 0.0,
            "conviction": 0.0,
            "final_score": 0.0,
            "abort_reason": "reversal_pending_advisory",
        },
        "weights": {"trend": 1.0, **weight_cfg},
        "asset_type": asset_type,
        "score_group": score_group,
        "trend_coherence": trend_detail,
        "directional_score": 0.0,
        "nondirectional_score": 0.0,
        "feed_status": feed_status,
        "abort_reason": "reversal_pending_advisory",
        "indeterminate_direction": False,
        "intermarket_confirmation": None,
        "intermarket_engine_a_delta": 0.0,
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
    """Return a clean zero-score result with diagnostics.

    For hard-abort reasons (ADX dead market, min-directional failure, ATR
    invalid, indeterminate trend, weighted TF tie) the tradable ``direction``
    field is forced to ``None`` and the executable flag is unset. The
    attempted direction is preserved in ``diagnostic_direction`` so audit /
    Marcus Reid output can still describe what evidence existed.
    """
    diagnostic_direction = direction
    is_hard_abort = reason in _NON_TRADABLE_ABORT_REASONS
    if is_hard_abort:
        direction = None
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
        "diagnostic_direction": diagnostic_direction,
        "signalExecutable": not is_hard_abort,
        "directionStatus": "diagnostic_only" if is_hard_abort else "ok",
        "regime": regime,
        "signal_status": "blocked" if is_hard_abort else "zero",
        "factor_scores": {"trend": 0.0, "momentum": 0.0, "addon": 0.0, "research_lab": 0.0, "mean_reversion": 0.0},
        "factor_breakdown": {
            "trend": 0.0,
            "momentum": 0.0,
            "addon": 0.0,
            "research_lab": 0.0,
            "mean_reversion": 0.0,
            "adx_multiplier": 0.0,
            "conviction": 0.0,
            "final_score": 0.0,
            "abort_reason": reason,
        },
        "weights": {"trend": 1.0, **weight_cfg},
        "asset_type": asset_type,
        "score_group": score_group,
        "engine_a_asset_diagnostics": asset_diagnostics,
        "structure_context_adjustment": {
            "enabled": bool(CONFIG.get("ENGINE_A_STRUCTURE_CONTEXT_ENABLED", False)),
            "applied": False,
            "adjusted_score": 0.0,
        },
        "engine_a_correlated_overlay_guard": {
            "enabled": bool(CONFIG.get("ENGINE_A_CORRELATED_OVERLAY_GUARD_ENABLED", True)),
            "warning": False,
            "capped": False,
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
        "research_lab_score_uplift": 0.0,
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
