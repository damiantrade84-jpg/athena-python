"""
Research-only Engine A/B context and recommendation helpers.

This module must stay isolated from live execution and live engine imports.  It
labels Research Lab results so reports can answer: which engine component works,
where it fails, and what should be tested next.
"""

from __future__ import annotations

import math
from dataclasses import replace
from typing import Iterable


_ZONE_BY_TF = {
    "M1": "scalp",
    "M5": "scalp",
    "M15": "scalp",
    "H1": "intra",
    "H4": "swing",
    "D1": "swing",
}


_ENGINE_A_BASELINE = {
    "ema_cross",
    "ema_alignment",
    "macd_direction",
    "pullback_ema",
}

_ENGINE_B_BASELINE = {
    "ob_bos",
    "structure_filters",
}


_STRATEGY_META: dict[str, dict[str, str]] = {
    "ema_cross": {
        "engine": "ENGINE_A",
        "component": "ema_coherence",
        "candidate_action": "KEEP_EXISTING",
        "structure_context": "trend_coherence",
        "source_indicator": "EMA cross",
    },
    "ema_alignment": {
        "engine": "ENGINE_A",
        "component": "ema_coherence",
        "candidate_action": "KEEP_EXISTING",
        "structure_context": "trend_pullback",
        "source_indicator": "EMA stack",
    },
    "macd_direction": {
        "engine": "ENGINE_A",
        "component": "rsi_macd_momentum",
        "candidate_action": "KEEP_EXISTING",
        "structure_context": "momentum_confirmation",
        "source_indicator": "MACD histogram",
    },
    "pullback_ema": {
        "engine": "ENGINE_A",
        "component": "entry_pullback",
        "candidate_action": "KEEP_EXISTING",
        "structure_context": "pullback_to_value",
        "source_indicator": "EMA pullback",
    },
    "london_breakout": {
        "engine": "ENGINE_A",
        "component": "session_breakout",
        "candidate_action": "RETEST",
        "structure_context": "session_range_break",
        "source_indicator": "Asian range break",
    },
    "prev_day_hl": {
        "engine": "ENGINE_A",
        "component": "breakout_filter",
        "candidate_action": "RETEST",
        "structure_context": "prior_range_break",
        "source_indicator": "Previous day high/low",
    },
    "session_opening_range": {
        "engine": "ENGINE_A",
        "component": "session_breakout",
        "candidate_action": "RETEST",
        "structure_context": "session_range_break",
        "source_indicator": "Opening range",
    },
    "bb_squeeze_breakout": {
        "engine": "ENGINE_A",
        "component": "volatility_filter",
        "candidate_action": "ADD_CANDIDATE",
        "structure_context": "volatility_compression",
        "source_indicator": "Bollinger width",
    },
    "atr_compression": {
        "engine": "ENGINE_A",
        "component": "volatility_filter",
        "candidate_action": "ADD_CANDIDATE",
        "structure_context": "volatility_compression",
        "source_indicator": "ATR compression",
    },
    "realized_vol_breakout": {
        "engine": "ENGINE_A",
        "component": "volatility_filter",
        "candidate_action": "ADD_CANDIDATE",
        "structure_context": "low_vol_breakout",
        "source_indicator": "Realized volatility",
    },
    "stochastic_cross": {
        "engine": "ENGINE_A",
        "component": "momentum_alternative",
        "candidate_action": "ADD_CANDIDATE",
        "structure_context": "momentum_turn",
        "source_indicator": "Stochastic K/D",
    },
    "aroon_trend": {
        "engine": "ENGINE_A",
        "component": "trend_alternative",
        "candidate_action": "ADD_CANDIDATE",
        "structure_context": "trend_strength",
        "source_indicator": "Aroon oscillator",
    },
    "chandelier_trend": {
        "engine": "ENGINE_A",
        "component": "trend_exit_alternative",
        "candidate_action": "ADD_CANDIDATE",
        "structure_context": "atr_trend_trailing",
        "source_indicator": "Chandelier exit",
    },
    "supertrend_follow": {
        "engine": "ENGINE_A",
        "component": "trend_alternative",
        "candidate_action": "ADD_CANDIDATE",
        "structure_context": "atr_trend_follow",
        "source_indicator": "Supertrend",
    },
    "fib_retracement": {
        "engine": "ENGINE_A",
        "component": "pullback_alternative",
        "candidate_action": "ADD_CANDIDATE",
        "structure_context": "fib_pullback",
        "source_indicator": "Fibonacci retracement",
    },
    "bollinger_touch": {
        "engine": "ENGINE_A",
        "component": "mean_reversion_candidate",
        "candidate_action": "WATCHLIST_ONLY",
        "structure_context": "range_extreme",
        "source_indicator": "Bollinger bands",
    },
    "rsi_extreme": {
        "engine": "ENGINE_A",
        "component": "mean_reversion_candidate",
        "candidate_action": "WATCHLIST_ONLY",
        "structure_context": "range_extreme",
        "source_indicator": "RSI",
    },
    "stochastic_divergence": {
        "engine": "ENGINE_A",
        "component": "divergence_candidate",
        "candidate_action": "WATCHLIST_ONLY",
        "structure_context": "range_extreme_divergence",
        "source_indicator": "Stochastic divergence",
    },
    "rsi_divergence": {
        "engine": "ENGINE_A",
        "component": "divergence_candidate",
        "candidate_action": "WATCHLIST_ONLY",
        "structure_context": "rsi_divergence",
        "source_indicator": "RSI divergence",
    },
    "obv_divergence": {
        "engine": "ENGINE_A",
        "component": "volume_divergence_candidate",
        "candidate_action": "WATCHLIST_ONLY",
        "structure_context": "volume_price_divergence",
        "source_indicator": "OBV divergence",
    },
    "ob_bos": {
        "engine": "ENGINE_B",
        "component": "structure_break",
        "candidate_action": "KEEP_EXISTING",
        "structure_context": "bos_ob_proxy",
        "source_indicator": "Swing break + order block proxy",
    },
    "structure_filters": {
        "engine": "ENGINE_B",
        "component": "entry_trigger",
        "candidate_action": "KEEP_EXISTING",
        "structure_context": "strong_close_fvg_proxy",
        "source_indicator": "Strong close + FVG proxy",
    },
    "vwap_deviation": {
        "engine": "ENGINE_B",
        "component": "location_quality",
        "candidate_action": "ADD_CANDIDATE",
        "structure_context": "value_deviation",
        "source_indicator": "VWAP deviation",
    },
    "cvd_momentum": {
        "engine": "ENGINE_D",
        "component": "scalp_momentum_proxy",
        "candidate_action": "ADD_CANDIDATE",
        "structure_context": "orderflow_momentum_proxy",
        "source_indicator": "CVD + VWAP proxy",
    },
    "vwap_reclaim": {
        "engine": "ENGINE_D",
        "component": "scalp_momentum_proxy",
        "candidate_action": "ADD_CANDIDATE",
        "structure_context": "vwap_reclaim",
        "source_indicator": "VWAP reclaim",
    },
    "micro_breakout": {
        "engine": "ENGINE_D",
        "component": "scalp_momentum_proxy",
        "candidate_action": "ADD_CANDIDATE",
        "structure_context": "micro_structure_break",
        "source_indicator": "Recent range break",
    },
    "ema_scalp_pullback": {
        "engine": "ENGINE_D",
        "component": "scalp_momentum_proxy",
        "candidate_action": "ADD_CANDIDATE",
        "structure_context": "ema_scalp_pullback",
        "source_indicator": "EMA pullback",
    },
}


def strategy_research_meta(strategy_name: str, family: str = "") -> dict[str, str]:
    meta = dict(_STRATEGY_META.get(strategy_name, {}))
    if not meta:
        if family == "engine_b_proxy":
            engine = "ENGINE_B"
        elif family in {"engine_d_proxy", "scalp_momentum_proxy"}:
            engine = "ENGINE_D"
        else:
            engine = "RESEARCH"
        meta = {
            "engine": engine,
            "component": family or "unclassified",
            "candidate_action": "RETEST",
            "structure_context": "unclassified",
            "source_indicator": strategy_name,
        }
    return meta


def infer_market_group(symbol: str, asset_class: str) -> str:
    if asset_class:
        if asset_class == "commodity":
            return "metals" if any(x in symbol for x in ("XAU", "XAG", "XPT", "XPD")) else "commodity"
        return asset_class
    if "USDT" in symbol:
        return "crypto"
    if "/" in symbol:
        return "forex"
    return "unknown"


def infer_pair_group(symbol: str, asset_class: str) -> str:
    if asset_class == "forex":
        majors = {"EUR/USD", "GBP/USD", "USD/JPY", "AUD/USD", "NZD/USD", "USD/CAD", "USD/CHF"}
        if symbol in majors:
            return "forex_majors"
        if symbol.endswith("/ZAR") or symbol.endswith("/MXN") or symbol.endswith("/SGD") or symbol.endswith("/BRL") or symbol.endswith("/INR"):
            return "forex_exotics"
        return "forex_crosses"
    if asset_class == "crypto":
        if symbol in {"BTC/USDT", "ETH/USDT"}:
            return "crypto_majors"
        if symbol in {"DOGE/USDT", "PEPE/USDT", "WIF/USDT"}:
            return "crypto_meme"
        return "crypto_alts"
    if asset_class == "commodity":
        return "metals" if any(x in symbol for x in ("XAU", "XAG", "XPT", "XPD")) else "commodity_other"
    return asset_class or "unknown"


def infer_zone(timeframe: str, existing: str = "") -> str:
    return existing or _ZONE_BY_TF.get(str(timeframe).upper(), "")


def infer_session_bucket(timeframe: str) -> str:
    tf = str(timeframe).upper()
    if tf in {"D1", "H4"}:
        return "multi_session"
    if tf in {"M1", "M5", "M15", "H1"}:
        return "intraday_session_sensitive"
    return "all"


def _finite(v: float) -> bool:
    try:
        return math.isfinite(float(v))
    except Exception:
        return False


def _recommendation(status: str, action: str, delta_pf: float, delta_oos: float, is_baseline: bool) -> str:
    status = (status or "").upper()
    action = (action or "").upper()
    if status == "STRONG_CANDIDATE":
        if is_baseline or action == "KEEP_EXISTING":
            return "KEEP"
        if action == "WATCHLIST_ONLY":
            return "WATCHLIST_ONLY"
        if action == "ADD_CANDIDATE":
            return "ADD"
        if (_finite(delta_pf) and delta_pf > 0) or (_finite(delta_oos) and delta_oos > 0):
            return "ADD"
        return "RETEST"
    if status == "WEAK_CANDIDATE":
        return "WATCHLIST_ONLY" if action == "WATCHLIST_ONLY" else "RETEST"
    if status == "NEEDS_MORE_DATA":
        return "RETEST"
    if is_baseline:
        return "REMOVE_OR_DEMOTE"
    return "REJECT"


def recommendation_from_fields(
    status: str,
    action: str = "",
    strategy_name: str = "",
    family: str = "",
    delta_pf: float = float("nan"),
    delta_oos: float = float("nan"),
) -> str:
    """Return the display/action recommendation for saved research rows."""
    meta_action = action
    if not meta_action:
        meta_action = strategy_research_meta(strategy_name, family).get("candidate_action", "")
    is_baseline = strategy_name in _ENGINE_A_BASELINE or strategy_name in _ENGINE_B_BASELINE
    return _recommendation(status, meta_action, delta_pf, delta_oos, is_baseline)


def annotate_research_results(results: Iterable, cfg: dict | None = None) -> list:
    """Return result objects enriched with engine/group/context recommendation fields."""
    enriched = []
    for m in results:
        meta = strategy_research_meta(getattr(m, "strategy_name", ""), getattr(m, "family", ""))
        market_group = infer_market_group(getattr(m, "symbol", ""), getattr(m, "asset_class", ""))
        pair_group = infer_pair_group(getattr(m, "symbol", ""), getattr(m, "asset_class", ""))
        zone = infer_zone(getattr(m, "timeframe", ""), getattr(m, "zone", ""))
        session_bucket = infer_session_bucket(getattr(m, "timeframe", ""))
        enriched.append(replace(
            m,
            engine=meta["engine"],
            engine_component=meta["component"],
            candidate_action=meta["candidate_action"],
            source_indicator=meta["source_indicator"],
            market_group=market_group,
            pair_group=pair_group,
            timeframe_zone=zone,
            zone=zone,
            session_bucket=session_bucket,
            structure_context=meta["structure_context"],
        ))

    baselines: dict[tuple[str, str, str, str], list] = {}
    for m in enriched:
        key = (m.engine, m.market_group, m.pair_group, m.timeframe_zone)
        if m.strategy_name in _ENGINE_A_BASELINE or m.strategy_name in _ENGINE_B_BASELINE:
            baselines.setdefault(key, []).append(m)

    def _avg(rows, attr):
        vals = [float(getattr(r, attr)) for r in rows if _finite(getattr(r, attr))]
        return sum(vals) / len(vals) if vals else float("nan")

    final = []
    for m in enriched:
        key = (m.engine, m.market_group, m.pair_group, m.timeframe_zone)
        base = baselines.get(key, [])
        base_pf = _avg(base, "profit_factor")
        base_oos = _avg(base, "oos_return")
        delta_pf = float("nan")
        delta_oos = float("nan")
        if _finite(m.profit_factor) and _finite(base_pf):
            delta_pf = float(m.profit_factor) - base_pf
        if _finite(m.oos_return) and _finite(base_oos):
            delta_oos = float(m.oos_return) - base_oos
        is_baseline = m.strategy_name in _ENGINE_A_BASELINE or m.strategy_name in _ENGINE_B_BASELINE
        rec = recommendation_from_fields(
            m.status,
            m.candidate_action,
            m.strategy_name,
            m.family,
            delta_pf,
            delta_oos,
        )
        final.append(replace(
            m,
            baseline_delta_pf=delta_pf,
            baseline_delta_oos=delta_oos,
            sample_ok=bool(getattr(m, "trade_count", 0) >= int((cfg or {}).get("min_trades", 20))),
            recommendation=rec,
        ))
    return final

