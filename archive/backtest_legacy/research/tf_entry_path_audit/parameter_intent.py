"""Parameter intent map for timeframe-sensitive Engine A/B indicators (Part 2)."""

from __future__ import annotations

from enum import Enum
from typing import Any


class ParameterIntent(str, Enum):
    CALENDAR_DURATION = "calendar-duration based"
    BAR_STRUCTURE = "bar-structure based"
    VOLATILITY = "volatility based"
    EXECUTION = "execution based"
    RISK_REWARD = "risk/reward based"
    SCORING_THRESHOLD = "scoring/threshold based"


class IndicatorProfile(str, Enum):
    CALENDAR_EQUIVALENT = "calendar_equivalent"
    BAR_NATIVE = "bar_native"
    VOLATILITY_NATIVE = "volatility_native"


# Canonical parameter intent map — research reference, not live config mutation.
PARAMETER_INTENT_MAP: list[dict[str, Any]] = [
    {
        "engine": "A",
        "parameter": "ENGINE_A_EMA_PERIODS_BY_CLASS.trend",
        "intent": ParameterIntent.CALENDAR_DURATION,
        "config_key": "ENGINE_A_EMA_PERIODS_BY_CLASS",
        "resolver": "factor_scoring._resolve_ema_periods",
    },
    {
        "engine": "A",
        "parameter": "ENGINE_A_EMA_PERIODS_BY_CLASS.momentum",
        "intent": ParameterIntent.CALENDAR_DURATION,
        "config_key": "ENGINE_A_EMA_PERIODS_BY_CLASS",
        "resolver": "factor_scoring._resolve_ema_periods",
    },
    {
        "engine": "A",
        "parameter": "ENGINE_A_EMA_PERIODS_BY_CLASS.long",
        "intent": ParameterIntent.BAR_STRUCTURE,
        "config_key": "ENGINE_A_EMA_PERIODS_BY_CLASS",
        "note": "200-bar anchor kept universal",
    },
    {
        "engine": "A",
        "parameter": "ENGINE_A_RSI_PERIOD_BY_CLASS",
        "intent": ParameterIntent.CALENDAR_DURATION,
        "config_key": "ENGINE_A_RSI_PERIOD_BY_CLASS",
        "resolver": "factor_scoring._resolve_rsi_period",
    },
    {
        "engine": "A",
        "parameter": "ENGINE_A_ATR_ADX_PERIODS_BY_CLASS.atr",
        "intent": ParameterIntent.VOLATILITY,
        "config_key": "ENGINE_A_ATR_ADX_PERIODS_BY_CLASS",
        "note": "ATR absolute scales with bar range; period is calendar-sensitive",
    },
    {
        "engine": "A",
        "parameter": "ENGINE_A_MACD_PARAMS_BY_CLASS",
        "intent": ParameterIntent.CALENDAR_DURATION,
        "config_key": "ENGINE_A_MACD_PARAMS_BY_CLASS",
        "note": "Deliberately universal bar counts in config",
    },
    {
        "engine": "A",
        "parameter": "ENGINE_A_SCORE_GROUP_THRESHOLDS",
        "intent": ParameterIntent.SCORING_THRESHOLD,
        "config_key": "ENGINE_A_SCORE_GROUP_THRESHOLDS",
    },
    {
        "engine": "A",
        "parameter": "ENGINE_A_V3_BACKTEST.MAX_HOLD_BARS",
        "intent": ParameterIntent.EXECUTION,
        "config_key": "ENGINE_A_V3_BACKTEST",
        "note": "Primary-TF bar count",
    },
    {
        "engine": "B",
        "parameter": "ENGINE_B_SWEEP_LOOKBACK_BARS",
        "intent": ParameterIntent.BAR_STRUCTURE,
        "config_key": "ENGINE_B_SWEEP_LOOKBACK_BARS",
        "consumer": "market_structure._detect_sweep",
    },
    {
        "engine": "B",
        "parameter": "ENGINE_B_BOS_LOOKBACK_BARS",
        "intent": ParameterIntent.BAR_STRUCTURE,
        "config_key": "ENGINE_B_BOS_LOOKBACK_BARS",
        "consumer": "market_structure BOS swing lookback",
    },
    {
        "engine": "B",
        "parameter": "NAKED_ENGINE.ob_lookback_bars",
        "intent": ParameterIntent.BAR_STRUCTURE,
        "config_key": "NAKED_ENGINE",
    },
    {
        "engine": "B",
        "parameter": "NAKED_ENGINE.zone_proximity_atr_mult",
        "intent": ParameterIntent.VOLATILITY,
        "config_key": "NAKED_ENGINE",
    },
    {
        "engine": "B",
        "parameter": "NAKED_ENGINE.engulfing_min_body_atr_mult",
        "intent": ParameterIntent.VOLATILITY,
        "config_key": "NAKED_ENGINE",
    },
    {
        "engine": "B",
        "parameter": "NAKED_ENGINE.min_rr / fallback_rr",
        "intent": ParameterIntent.RISK_REWARD,
        "config_key": "NAKED_ENGINE.style_profiles",
    },
    {
        "engine": "B",
        "parameter": "ENGINE_B_BT_EXIT max_hold_bars",
        "intent": ParameterIntent.EXECUTION,
        "config_key": "ENGINE_B_BT_EXIT",
        "note": "Defined in H4 bar units; converted at monitor TF",
    },
    {
        "engine": "B",
        "parameter": "ENGINE_B_MIN/MAX_SL_ATR_DEFAULT",
        "intent": ParameterIntent.VOLATILITY,
        "config_key": "ENGINE_B_ATR_SL_CLAMPS",
    },
    {
        "engine": "B",
        "parameter": "NAKED_ENGINE.min_score",
        "intent": ParameterIntent.SCORING_THRESHOLD,
        "config_key": "NAKED_ENGINE.style_profiles",
    },
]

_TF_HOURS = {"M15": 0.25, "H1": 1.0, "H4": 4.0, "D1": 24.0}


def tf_ratio(from_tf: str, to_tf: str) -> float:
    """Calendar hours ratio between timeframes."""
    f = _TF_HOURS.get(str(from_tf).upper(), 1.0)
    t = _TF_HOURS.get(str(to_tf).upper(), 1.0)
    if t <= 0:
        return 1.0
    return f / t


def scale_period_for_profile(
    base_period: int,
    *,
    signal_tf: str,
    target_tf: str,
    profile: IndicatorProfile,
) -> int:
    """Return scaled period for a diagnostic profile family."""
    base = max(2, int(base_period))
    if profile == IndicatorProfile.BAR_NATIVE:
        return base
    if profile == IndicatorProfile.CALENDAR_EQUIVALENT:
        ratio = tf_ratio(target_tf, signal_tf)
        return max(2, int(round(base * ratio)))
    # volatility_native: use sqrt scaling as conservative default
    ratio = tf_ratio(target_tf, signal_tf)
    return max(2, int(round(base * (ratio**0.5))))


def profile_context_manager(profile: IndicatorProfile):
    """Return context manager applying indicator profile patches."""
    from athena_research.tf_entry_path_audit.patches import indicator_profile_patch

    return indicator_profile_patch(profile)


def intent_map_as_rows() -> list[dict[str, str]]:
    return [
        {
            "engine": row["engine"],
            "parameter": row["parameter"],
            "intent": row["intent"].value if isinstance(row["intent"], ParameterIntent) else str(row["intent"]),
            "config_key": row.get("config_key", ""),
            "note": row.get("note", ""),
        }
        for row in PARAMETER_INTENT_MAP
    ]
