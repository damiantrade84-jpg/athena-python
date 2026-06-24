"""scoring.py — Confluence scoring engine, session detection, and divergence detection.

Depends on: config.CONFIG, indicators (calc_* functions).
"""

import logging
import warnings
from datetime import datetime, timezone
from typing import Any, Callable, List, Optional, TypedDict

from config import CONFIG
from engine_a_groups import ENGINE_A_KNOWN_SCORE_GROUPS
from indicators import (
    calc_rsi,
)

log = logging.getLogger("athena")


class FactorDiagnosticsPayload(TypedDict, total=False):
    directionalScore: float | None
    nondirectionalScore: float | None
    feedStatus: dict[str, Any]
    addon_type: str | None
    addon_value: float | None
    momentumQuality: float | None
    adxMultiplier: float | None
    conviction: float | None
    signalStatus: str | None
    factorBreakdown: dict[str, Any]
    abortReason: str | None
    engineVersion: str
    scorerSelected: str


def _safe_feed_mult(feed_status: dict, key: str) -> float | None:
    """Parse feed_status multiplier strings (e.g. di_align '0.30') for UI diagnostics."""
    if not isinstance(feed_status, dict):
        return None
    raw = feed_status.get(key)
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _tf_score_proxy(snap: dict) -> dict | None:
    """Build a lightweight per-timeframe score proxy for timeframe_alignment.

    Uses EMA alignment, RSI bias, and MACD direction to produce a simple
    directional score in the same scale as factor_result['final_score'].
    Returns dict with 'final_score' or None if snap lacks data.
    """
    if not snap:
        return None
    components = []
    # EMA alignment: close vs ema50 vs ema200
    close = snap.get("close")
    ema50 = snap.get("ema50")
    ema200 = snap.get("ema200")
    if close is not None and ema50 is not None and ema200 is not None and ema200 != 0:
        if close > ema50 > ema200:
            components.append(1.0)
        elif close < ema50 < ema200:
            components.append(-1.0)
        else:
            components.append(0.0)
    # RSI bias
    rsi = snap.get("rsi")
    if rsi is not None:
        rsi_bias = (rsi - 50) / 50
        components.append(max(-1.0, min(1.0, rsi_bias)))
    # MACD direction
    macd_hist = snap.get("macdHist")
    if macd_hist is not None:
        if macd_hist > 0:
            components.append(1.0)
        elif macd_hist < 0:
            components.append(-1.0)
        else:
            components.append(0.0)
    if not components:
        return None
    avg = sum(components) / len(components)
    return {"final_score": round(avg, 4)}


def _vote_sign(val) -> int:
    """Map a numeric factor value to a tri-state vote."""
    if val > 0:
        return 1
    if val < 0:
        return -1
    return 0


_LEGACY_VOTE_FACTOR_MAP = {
    "D1 Trend Gate": "trend",
    "D1 Momentum": "momentum",
    "D1 Weinstein Stage": "weinstein",
    "H4 MACD Momentum": "momentum",
    "H4 RSI Zone": "rsi",
    "H4 Stochastic": "stoch",
    "H4 Fib Level": "fib",
    "H1 EMA Entry": "ema",
    "H1 BB Pullback": "bb",
    "H1 RSI Divergence": "rsi_div",
}

_MAJOR_FOREX = {
    "EUR/USD", "GBP/USD", "USD/JPY", "AUD/USD", "NZD/USD", "USD/CAD", "USD/CHF"
}
_FOREX_CROSSES = {
    "EUR/GBP", "EUR/JPY", "GBP/JPY", "AUD/JPY", "EUR/AUD", "GBP/AUD", "EUR/CHF", "USD/SGD",
    "AUD/CHF", "AUD/NZD",
}
_EXOTIC_FOREX = {"USD/ZAR", "USD/MXN", "USD/BRL", "USD/INR"}
_PRECIOUS_TRACKERS = {"XAU/USD", "XAG/USD", "GLD", "SLV", "GDX"}
_ENERGY_OIL = {"WTI Oil", "Brent Oil", "USO", "XLE"}
# Industrial base metals — trend well, route to STABLE tier.
_BASE_METALS = {"Aluminium", "Lead", "Nickel", "Zinc"}
# Soft commodities + livestock — Athena edge unaudited; route to EXOTIC tier.
_SOFTS = {"Cattle", "Cocoa", "Coffee", "Corn", "Cotton", "Soybeans", "Sugar", "Wheat"}
_US_INDICES_TRACKERS = {"NASDAQ-100", "S&P 500", "Dow Jones", "SPY", "QQQ", "DIA", "SOXX"}
_EU_INDICES = {"DAX", "DAX 40", "UK100", "FTSE 100"}
_ASIAN_INDICES = {"ASX 200", "Nikkei 225", "Hang Seng"}
_US_STOCK_CUSTOM = {
    "AAPL", "TSLA", "NVDA", "MSFT", "AMZN", "META", "GOOG", "JPM", "V", "XOM",
    "NFLX", "AMD", "CRM", "DIS", "BA", "COIN", "PYPL", "INTC", "UBER", "PLTR"
}
_ALTCOIN_MAJORS = {
    "SOL/USDT", "ADA/USDT", "AVAX/USDT", "LINK/USDT", "POL/USDT", "BNB/USDT",
    "DOT/USDT", "LTC/USDT", "SUI/USDT", "NEAR/USDT", "APT/USDT", "INJ/USDT",
    "RENDER/USDT",
    # 2026-06-24: promoted from crypto_other — liquid alts that previously scored
    # against the stricter crypto_other 2.2 bar despite high liquidity. XRP is an
    # ENGINE_B_HOTLIST_CRYPTO_ANCHORS member. Effect: Engine A threshold 2.2->2.0,
    # min_directional 0.24->0.20, RSI bounds 75/25->80/20. Reversible (remove names).
    "XRP/USDT", "ARB/USDT", "OP/USDT", "UNI/USDT",
}


def get_pair_profile(pair: dict) -> dict:
    """Return pair-specific profile overrides keyed by display or symbol."""
    profiles = CONFIG.get("PAIR_PROFILES", {}) or {}
    return profiles.get(pair.get("display")) or profiles.get(pair.get("symbol")) or {}


def get_pair_score_group(pair: dict) -> str:
    """Resolve subgroup used for scoring and confluence routing."""
    if pair.get("score_group"):
        return str(pair.get("score_group"))
    profile = get_pair_profile(pair)
    if profile.get("score_group"):
        return str(profile.get("score_group"))

    from engine_a_groups import ENGINE_A_KNOWN_SCORE_GROUPS

    display = pair.get("display", "")
    ptype = pair.get("type", "")

    if ptype == "forex":
        if display in _MAJOR_FOREX:
            return "forex_majors"
        if display in _FOREX_CROSSES:
            return "forex_crosses"
        if display in _EXOTIC_FOREX:
            return "forex_exotics"
        return "forex_other"
    if ptype == "crypto":
        if display == "BTC/USDT":
            return "crypto_btc"
        if display == "ETH/USDT":
            return "crypto_eth"
        if display == "DOGE/USDT":
            return "crypto_doge"
        if display in _ALTCOIN_MAJORS:
            return "crypto_alt_majors"
        return "crypto_other"
    if ptype == "commodity":
        if display in _PRECIOUS_TRACKERS:
            return "precious_trackers"
        if display in _ENERGY_OIL:
            return "energy_oil"
        if display == "Nat Gas":
            return "nat_gas"
        if display == "Copper":
            return "copper"
        if display in {"XPT/USD", "XPD/USD"}:
            return "pgm_metals"
        if display in _BASE_METALS:
            return "base_metals"
        if display in _SOFTS:
            return "softs"
        return "commodity_other"
    if ptype == "index":
        if display in _US_INDICES_TRACKERS:
            return "us_indices_trackers"
        if display in _EU_INDICES:
            return "eu_indices"
        if display in _ASIAN_INDICES:
            return "asian_indices"
        return "index_other"
    if ptype == "stock":
        if display in _US_STOCK_CUSTOM:
            return "us_stock_single"
        if display == "TLT":
            return "bond_tlt"
        if display in {"IWM", "EEM"}:
            return "smallcap_em_etf"
        if display in _PRECIOUS_TRACKERS:
            return "precious_trackers"
        if display in _ENERGY_OIL:
            return "energy_oil"
        if display in _US_INDICES_TRACKERS:
            return "us_indices_trackers"
        return "stock_other"
    if ptype in ("etf", "etf_bond"):
        if display == "TLT" or ptype == "etf_bond":
            return "bond_tlt"
        if display in {"IWM", "EEM"}:
            return "smallcap_em_etf"
        if display in _PRECIOUS_TRACKERS:
            return "precious_trackers"
        if display in _ENERGY_OIL:
            return "energy_oil"
        if display in _US_INDICES_TRACKERS:
            return "us_indices_trackers"
        return "stock_other"
    _fallback = f"{ptype}_other" if ptype else "unknown"
    if _fallback not in ENGINE_A_KNOWN_SCORE_GROUPS:
        log.warning(
            "get_pair_score_group: unknown group '%s' for %s (type=%s) — "
            "no ENGINE_A_SCORE_GROUP_THRESHOLDS entry; falling back to legacy tier",
            _fallback, display, ptype,
        )
        return "unknown"
    return _fallback


ENGINE_A_ASSET_CLASS_SCORE_GROUPS: dict[str, tuple[str, ...]] = {
    "forex": ("forex_majors", "forex_crosses", "forex_exotics", "forex_other"),
    "crypto": ("crypto_btc", "crypto_eth", "crypto_doge", "crypto_alt_majors", "crypto_other"),
    "commodity": (
        "precious_trackers",
        "energy_oil",
        "nat_gas",
        "copper",
        "pgm_metals",
        "base_metals",
        "softs",
        "commodity_other",
    ),
    "index": ("us_indices_trackers", "eu_indices", "asian_indices", "index_other"),
    "stock": ("us_stock_single", "bond_tlt", "smallcap_em_etf", "stock_other"),
}

ENGINE_A_ASSET_CLASS_PRIMARY_GROUP: dict[str, str] = {
    "forex": "forex_majors",
    "crypto": "crypto_btc",
    "commodity": "commodity_other",
    "index": "us_indices_trackers",
    "stock": "stock_other",
}


def expand_engine_a_threshold_updates(updates: dict) -> dict[str, float]:
    """Map dashboard asset-class keys to score_group keys used by live gates."""
    from engine_a_groups import ENGINE_A_KNOWN_SCORE_GROUPS

    expanded: dict[str, float] = {}
    for key, raw_val in (updates or {}).items():
        try:
            val = round(float(raw_val), 4)
        except (TypeError, ValueError):
            continue
        groups = ENGINE_A_ASSET_CLASS_SCORE_GROUPS.get(str(key))
        if groups:
            for group in groups:
                expanded[group] = val
            continue
        if str(key) in ENGINE_A_KNOWN_SCORE_GROUPS or str(key) == "default":
            expanded[str(key)] = val
    return expanded


def aggregate_engine_a_thresholds_for_dashboard(thresholds: dict) -> dict[str, float]:
    """Summarize score_group thresholds for per-asset-class dashboard fields."""
    src = thresholds or {}
    out: dict[str, float] = {}
    for asset_class, groups in ENGINE_A_ASSET_CLASS_SCORE_GROUPS.items():
        primary = ENGINE_A_ASSET_CLASS_PRIMARY_GROUP.get(asset_class)
        if primary and primary in src:
            out[asset_class] = float(src[primary])
            continue
        for group in groups:
            if group in src:
                out[asset_class] = float(src[group])
                break
    return out


def current_engine_a_threshold_for_asset_class(asset_class: str, thresholds: dict | None = None) -> float:
    """Return the representative live threshold for an asset class."""
    src = thresholds if thresholds is not None else (CONFIG.get("ENGINE_A_SCORE_GROUP_THRESHOLDS") or {})
    aggregated = aggregate_engine_a_thresholds_for_dashboard(src)
    return float(aggregated.get(asset_class, 0.0) or 0.0)


# Every value returned by get_pair_score_group() must have an explicit entry in
# ENGINE_A_SCORE_GROUP_THRESHOLDS (config.yaml / config.py). The "default" key is
# only for forward-compatible unknown groups — not a substitute for listed groups.
def _resolve_class_keyed(mapping, score_group: str | None, asset_type: str, default):
    """Resolve class-keyed config by score_group, then asset_type, then default."""
    if not isinstance(mapping, dict):
        return default
    if score_group and score_group in mapping:
        return mapping[score_group]
    if asset_type in mapping:
        return mapping[asset_type]
    if "default" in mapping:
        return mapping["default"]
    return default


def get_pair_level_atr_class(pair: dict) -> str:
    """Resolve the ATR multiplier class used for SL/TP levels.

    This stays separate from pair["type"] so ETFs can keep their stock
    feed/execution identity while using ETF-calibrated ATR multipliers.
    """
    profile = get_pair_profile(pair)
    profile_class = profile.get("atr_level_class")
    if profile_class:
        return str(profile_class)

    display = pair.get("display", "")
    symbol = pair.get("symbol", "")
    by_display = CONFIG.get("ENGINE_A_ATR_LEVEL_CLASS_BY_DISPLAY", {}) or {}
    if display in by_display:
        return str(by_display[display])
    if symbol in by_display:
        return str(by_display[symbol])

    score_group = get_pair_score_group(pair)
    by_group = CONFIG.get("ENGINE_A_ATR_LEVEL_CLASS_BY_SCORE_GROUP", {}) or {}
    if score_group in by_group:
        return str(by_group[score_group])

    return str(pair.get("type", "stock") or "stock")


# 3-tier Engine A threshold system.
#   VOLATILE (2.0) — crypto class, nat_gas, crypto_doge: high baseline noise.
#   EXOTIC   (1.7) — forex_exotics, softs: thin liquidity / unaudited edge.
#   STABLE   (1.5) — everything else.
# Pair profile min_confluence is the only runtime override.
_TIER_VOLATILE = 2.0
_TIER_EXOTIC = 1.7
_TIER_STABLE = 1.5

_PAIR_OVERRIDES = {
    "XAU/USD": 1.5,
    "XAG/USD": 1.5,
}


def _configured_score_threshold(pair: dict) -> float | None:
    """Return a config-backed Engine A threshold, or None when absent.

    Resolution order: pair display/symbol override, then score_group, then
    ``default``. Asset ``type`` is intentionally not used here so a broad
    ``crypto`` / ``forex`` key cannot mask per-group floors. When this returns
    None, ``get_score_threshold`` falls back to the legacy 3-tier resolver.
    """
    display = pair.get("display", "")
    symbol = pair.get("symbol", "")
    score_group = get_pair_score_group(pair)

    pair_thresholds = CONFIG.get("ENGINE_A_PAIR_THRESHOLDS", {}) or {}
    for key in (display, symbol):
        if key in pair_thresholds:
            return float(pair_thresholds[key])

    group_thresholds = CONFIG.get("ENGINE_A_SCORE_GROUP_THRESHOLDS", {}) or {}
    for key in (score_group, "default"):
        if key in group_thresholds:
            return float(group_thresholds[key])
    return None


def _get_threshold_tier(pair: dict) -> float:
    """Return the confluence threshold for a pair (3-tier system)."""
    display = pair.get("display", "")
    ptype = pair.get("type", "")
    score_group = get_pair_score_group(pair)

    # 1. Pair-specific overrides
    if display in _PAIR_OVERRIDES:
        return float(_PAIR_OVERRIDES[display])

    # 2. Volatile tier
    if ptype in ("crypto",) or score_group in ("nat_gas", "crypto_doge"):
        return _TIER_VOLATILE

    # 3. Exotic tier — thin liquidity or unaudited edge.
    if score_group in ("forex_exotics", "softs"):
        return _TIER_EXOTIC

    # 4. Stable tier (everything else)
    return _TIER_STABLE


def get_score_threshold(pair: dict, regime: str | None = None) -> float:
    """Resolve score threshold (live and backtest share the same value).

    Profile override -> pair/group config -> 3-tier fallback.

    When ENGINE_A_REGIME_DYNAMIC_THRESHOLDS.ENABLED is true and ``regime`` is
    provided, applies regime-based multipliers to the resolved threshold:
      - TRENDING: 10% easier (0.90 multiplier)
      - RANGING: 10% harder (1.10 multiplier)
      - HIGH_VOLATILITY: 15% harder (1.15 multiplier)
    """
    profile = get_pair_profile(pair)
    configured = _configured_score_threshold(pair)
    if configured is not None:
        base_threshold = configured
    else:
        # Fallback 3-tier system for older configs.
        base_threshold = _get_threshold_tier(pair)

    # Pair profiles may raise a threshold by default. Lowering below the
    # configured/group floor requires an explicit per-profile opt-in so one
    # stale pair override cannot silently bypass the global scan tier.
    if profile.get("min_confluence") is not None:
        profile_threshold = float(profile.get("min_confluence"))
        if profile_threshold >= base_threshold or profile.get("allow_lower_threshold") is True:
            if profile_threshold < base_threshold:
                log.warning(
                    "[ENGINE_A_THRESHOLD] %s lowered score threshold %.4f -> %.4f via explicit allow_lower_threshold",
                    pair.get("display") or pair.get("symbol") or "?",
                    base_threshold,
                    profile_threshold,
                )
            base_threshold = profile_threshold

    # Apply regime-dependent dynamic thresholds if enabled
    dynamic_cfg = CONFIG.get("ENGINE_A_REGIME_DYNAMIC_THRESHOLDS") or {}
    if dynamic_cfg.get("ENABLED", False) and regime:
        trending_mult = float(dynamic_cfg.get("TRENDING_MULTIPLIER", 0.90))
        ranging_mult = float(dynamic_cfg.get("RANGING_MULTIPLIER", 1.10))
        high_vol_mult = float(dynamic_cfg.get("HIGH_VOLATILITY_MULTIPLIER", 1.15))

        if regime == "TRENDING":
            base_threshold *= trending_mult
        elif regime == "RANGING":
            base_threshold *= ranging_mult
        elif regime == "HIGH_VOLATILITY":
            base_threshold *= high_vol_mult

    return base_threshold


def engine_a_regime_label_for_threshold(signal_result: dict | None) -> str | None:
    """Extract the Engine A regime label used by dynamic threshold resolution."""
    if not isinstance(signal_result, dict):
        return None
    label = signal_result.get("regimeName")
    if label is None:
        regime = signal_result.get("regime")
        if isinstance(regime, dict):
            label = regime.get("label")
        elif isinstance(regime, str):
            label = regime
    if label is None:
        return None
    text = str(label).strip().upper()
    return text or None


def get_min_confluence_threshold(pair: dict) -> float:
    """Legacy wrapper for live scan threshold resolution."""
    return get_score_threshold(pair)


def get_min_confidence_threshold(pair: dict) -> float:
    """Resolve Engine A trade-tier minimum confidence (0-1) by pair/group."""
    display = pair.get("display", "")
    symbol = pair.get("symbol", "")
    score_group = get_pair_score_group(pair)

    pair_mins = CONFIG.get("ENGINE_A_PAIR_MIN_CONFIDENCE", {}) or {}
    for key in (display, symbol):
        if key in pair_mins:
            return float(pair_mins[key])

    group_mins = CONFIG.get("ENGINE_A_SCORE_GROUP_MIN_CONFIDENCE", {}) or {}
    for key in (score_group, "default"):
        if key in group_mins:
            return float(group_mins[key])

    legacy = CONFIG.get("ENGINE_A_TRADE_MIN_CONFIDENCE")
    if legacy is not None:
        return float(legacy)
    return float(CONFIG.get("CONFIDENCE_THRESHOLD", 0.55))


def get_backtest_min_score_threshold(pair: dict) -> float:
    """Legacy wrapper for Engine A backtest gate (parity with live)."""
    return get_score_threshold(pair)


def annotate_multiplier_flips(
    attribution: dict, final_score: float | None, threshold: float | None
) -> dict:
    """Mark which multipliers alone flip the score across the trade threshold.

    Report-only telemetry: for each entry from factor_scoring's
    multiplier_attribution, ``would_flip_decision`` is true when removing that
    single multiplier moves the score to the other side of the threshold.
    Mutates and returns ``attribution``; never changes scores or gating.
    """
    if not isinstance(attribution, dict) or final_score is None or threshold is None:
        return attribution
    try:
        passed = float(final_score) >= float(threshold)
    except (TypeError, ValueError):
        return attribution
    for entry in attribution.values():
        if not isinstance(entry, dict):
            continue
        score_without = entry.get("score_without")
        if score_without is None:
            continue
        try:
            entry["would_flip_decision"] = bool(
                (float(score_without) >= float(threshold)) != passed
            )
        except (TypeError, ValueError):
            continue
    return attribution


def pair_filter_enabled(pair: dict, filter_name: str) -> bool:
    disabled = set(get_pair_profile(pair).get("disable_filters", []) or [])
    return filter_name not in disabled


def _resolve_trend_state(
    factor_result: dict | None,
    h4_snap: dict | None,
    pair_type: str,
) -> tuple[str, str, float | None]:
    """Build ``(trendState, trendStateAdxSource, adxValue)``.

    Engine A's ``_adx_gate`` may select D1 or max(D1, H4) as the ADX source
    for scoring (per asset class / score group). Previously ``trendState``
    always read H4 ADX from the indicator snap which produced inconsistent
    regime labels and could trigger different downstream behavior than the
    value that actually gated Engine A scoring. We now reuse the selected
    ADX value/source from the factor diagnostics whenever available, and
    fall back to H4 ADX only when those diagnostics are absent.
    """
    factor_adx = None
    factor_source = None
    if isinstance(factor_result, dict):
        raw_factor_adx = factor_result.get("adx_value")
        if raw_factor_adx is not None:
            try:
                factor_adx = float(raw_factor_adx)
            except (TypeError, ValueError):
                factor_adx = None
        factor_source = factor_result.get("adx_source")

    if factor_adx is not None:
        adx_val = factor_adx
        source = str(factor_source) if factor_source else "unknown"
    else:
        raw_h4 = (h4_snap or {}).get("adx") if isinstance(h4_snap, dict) else None
        try:
            adx_val = float(raw_h4) if raw_h4 is not None else None
        except (TypeError, ValueError):
            adx_val = None
        source = "h4_fallback"

    if adx_val is None:
        return "UNKNOWN", source, None

    _rng = CONFIG["RANGING"].get(pair_type, CONFIG["RANGING"]["commodity"])
    # Per-asset-class TRENDING/DEVELOPING ADX cutoffs mirror RANGING's per-class
    # structure so a crypto/forex trend that the class-aware ADX *gate* fully
    # credits (ADX_TREND_MIN_CLASS: crypto 18 / forex 20) is not mislabelled by
    # the stock-grade universal cutoffs (35/25). Absent a class entry these fall
    # back to the universal scalars, so default behavior is unchanged; per-class
    # values are evidence-gated (n>=30 closed-trade outcomes per bucket).
    _trending_cls = CONFIG.get("TRENDING_ADX_CLASS") or {}
    _developing_cls = CONFIG.get("DEVELOPING_ADX_CLASS") or {}
    _trending_adx = float(_trending_cls.get(pair_type, CONFIG.get("TRENDING_ADX", 35)))
    _developing_adx = float(_developing_cls.get(pair_type, CONFIG.get("DEVELOPING_ADX", 25)))
    if adx_val >= _trending_adx:
        trend_state = "TRENDING"
    elif adx_val >= _developing_adx:
        trend_state = "DEVELOPING"
    elif adx_val >= _rng["dead"]:
        trend_state = "RANGING"
    else:
        trend_state = "DEAD RANGING"
    return trend_state, source, adx_val


def classify_signal_setup(
    direction: str,
    entry_mode: str,
    squeeze_bonus: bool = False,
    atr_breakout: bool = False,
    votes: dict | None = None,
) -> str:
    """Classify the setup so pair-specific routing is easier to audit.

    Uses structured flags (squeeze_bonus, atr_breakout) set during scoring rather
    than re-parsing warning strings, so renaming warning text never silently breaks classification.

    NOTE: entry_mode is accepted for API stability but the only caller (calc_confluence)
    always passes "trend". Mean reversion classification is handled by the factor engine's
    mean_reversion factor, not here.
    """
    if squeeze_bonus or atr_breakout:
        return "breakout"
    dir_vote = 1 if direction == "LONG" else -1
    votes = votes or {}
    if (
        votes.get("H1 BB Pullback") == dir_vote
        and votes.get("D1 Trend Gate") == dir_vote
    ):
        return "trend_pullback"
    return "trend_continuation"


def get_session(
    bar_time: str | None = None,
    *,
    asset_class: str = "forex",
    symbol: str = "",
    venue: str | None = None,
) -> dict:
    """Determine session label (liquidity bucket for UI).

    bar_time: ISO timestamp of a specific bar (e.g. backtests). If omitted, uses
              current UTC — use this for live scans so the badge matches scan time.

    When SESSION_QUALITY_ENABLED is true, delegates to session_contract.classify_session
    and maps the result to the legacy {name, quality, color} shape.
    """
    if CONFIG.get("SESSION_QUALITY_ENABLED", False):
        try:
            from session_contract import classify_session, session_to_legacy_dict
            sc = classify_session(
                symbol=symbol,
                asset_class=asset_class,
                timestamp_utc=bar_time,
                venue=venue,
            )
            return session_to_legacy_dict(sc)
        except Exception:
            log.debug("session_contract fallback to legacy get_session", exc_info=True)

    if bar_time:
        try:
            dt = datetime.fromisoformat(bar_time.replace("Z", "+00:00"))
            h = dt.hour
        except Exception:
            h = datetime.now(timezone.utc).hour
    else:
        h = datetime.now(timezone.utc).hour
    if 7 <= h < 9:
        return {"name": "London Open", "quality": "high", "color": "#22c55e"}
    if 13 <= h < 16:
        return {"name": "London/NY Overlap", "quality": "high", "color": "#22c55e"}
    if 9 <= h < 13:
        return {"name": "London", "quality": "medium", "color": "#3b82f6"}
    if 16 <= h < 22:
        return {"name": "New York", "quality": "medium", "color": "#3b82f6"}
    if asset_class == "crypto":
        # Crypto is 24/7 — do not apply forex off-hours "low" bucket to confidence.
        if 14 <= h < 19:
            return {"name": "EU/US Crypto Peak", "quality": "high", "color": "#22c55e"}
        if 1 <= h < 5:
            return {"name": "Asia Crypto", "quality": "medium", "color": "#3b82f6"}
        if 9 <= h < 14:
            return {"name": "EU Morning Crypto", "quality": "medium", "color": "#3b82f6"}
        if 19 <= h < 22:
            return {"name": "US Afternoon Crypto", "quality": "medium", "color": "#3b82f6"}
        return {"name": "Crypto Transition", "quality": "medium", "color": "#3b82f6"}
    return {"name": "Asian / Off-Hours", "quality": "low", "color": "#f59e0b"}


def detect_div(d1c: list, h4c: list, h1c: list) -> list:
    """Detect H4 RSI divergence and H1 volume divergence. Returns list of warning strings."""
    w = []
    try:
        # Use 40 bars so RSI(14) is fully warmed before the comparison zones.
        # With 20 bars the middle-third indices (6-12) fall entirely inside the
        # 14-bar None-padding zone and no divergence can ever be detected.
        h4 = h4c[-40:]
        cl = [c["close"] for c in h4]
        lows = [c["low"] for c in h4]
        rsi = calc_rsi(cl, 14)
        pr = [c["high"] for c in h4]
        n = len(pr)
        if n >= 10 and rsi[-1] is not None:
            t = n // 3
            # Prior zone: middle third; current zone: final third
            prior_highs = pr[t : 2 * t]
            prior_lows = lows[t : 2 * t]
            prior_rsi = rsi[t : 2 * t]
            curr_highs = pr[2 * t :]
            curr_lows = lows[2 * t :]
            if prior_highs and prior_rsi and curr_highs:
                peak_idx = prior_highs.index(max(prior_highs))
                trough_idx = prior_lows.index(min(prior_lows))
                rsi_at_peak = prior_rsi[peak_idx] if peak_idx < len(prior_rsi) else None
                rsi_at_trough = prior_rsi[trough_idx] if trough_idx < len(prior_rsi) else None
                curr_high = max(curr_highs)
                curr_low = min(curr_lows)
                # Bearish div: current zone makes higher high, but RSI is lower
                if rsi_at_peak is not None and curr_high > max(prior_highs) and rsi[-1] < rsi_at_peak:
                    w.append("H4 RSI Bearish Divergence")
                # Bullish div: current zone makes lower low, but RSI is higher
                if rsi_at_trough is not None and curr_low < min(prior_lows) and rsi[-1] > rsi_at_trough:
                    w.append("H4 RSI Bullish Divergence")
    except Exception as e:
        log.warning(f"detect_div H4: {e}")
    try:
        h1 = h1c[-20:]
        vols = [c["vol"] for c in h1]
        pr = [c["close"] for c in h1]
        n = len(pr)
        if n >= 10:
            m = n // 2
            if pr[-1] > pr[0] and vols[-1] < vols[m] and vols[-1] > 0:
                w.append("H1 Vol Div - rising price, falling vol")
            if pr[-1] < pr[0] and vols[-1] > vols[m] and vols[-1] > 0:
                w.append("H1 Vol Div - falling price, rising vol")
    except Exception as e:
        log.warning(f"detect_div H1: {e}")
    return w


# ── Correlation clusters ─────────────────────────────────────────────────────
CORR_CLUSTERS: dict = {
    "crypto_major": [
        "BTC/USDT",
        "ETH/USDT",
        "XRP/USDT",
        "BNB/USDT",
        "SOL/USDT",
        "ADA/USDT",
        "DOGE/USDT",
        "AVAX/USDT",
        "DOT/USDT",
        "POL/USDT",
        "LTC/USDT",
        "LINK/USDT",
        "SUI/USDT",
        "APT/USDT",
        "NEAR/USDT",
        "INJ/USDT",
        "RENDER/USDT",
        "AAVE/USDT",
        "ALGO/USDT",
        "ATOM/USDT",
        "BCH/USDT",
        "ETC/USDT",
        "TRX/USDT",
        "XLM/USDT",
        "UNI/USDT",
        "FIL/USDT",
        "ICP/USDT",
        "HBAR/USDT",
        "ARB/USDT",
        "OP/USDT",
        "SEI/USDT",
    ],
    "metals": ["XAU/USD", "XAG/USD", "GLD", "SLV"],
    "energy": ["XOM", "XLE", "USO", "WTI Oil", "Brent Oil"],
    "forex_usd": [
        "EUR/USD",
        "GBP/USD",
        "USD/JPY",
        "AUD/USD",
        "NZD/USD",
        "USD/CHF",
        "USD/CAD",
        "USD/ZAR",
        "USD/MXN",
        "USD/SGD",
    ],
    "forex_jpy": ["EUR/JPY", "GBP/JPY", "AUD/JPY"],
    "jse": [
        "Naspers",
        "Sasol",
        "Std Bank",
        "Anglo Am",
        "MTN Group",
        "Shoprite",
        "Richemont",
        "FirstRand",
        "Absa",
        "Capitec",
        "Prosus",
        "Gold Fields",
        "AngloGold",
        "Sibanye",
    ],
    "us_tech": ["AAPL", "TSLA", "NVDA", "MSFT", "AMZN", "META", "GOOG"],
    "us_sp500": ["SPY", "QQQ", "S&P 500", "Nasdaq"],
    "nat_gas": ["Nat Gas"],
    "softs": ["Cocoa", "Coffee", "Corn", "Cotton", "Soybeans", "Sugar", "Wheat"],
    "base_metals": ["Aluminium", "Lead", "Nickel", "Zinc"],
}

# Reverse lookup: pair_display → cluster_name (built once at import time)
_PAIR_TO_CLUSTER: dict = {
    pair: cluster
    for cluster, members in CORR_CLUSTERS.items()
    for pair in members
}


def _configured_corr_clusters() -> dict:
    cfg = CONFIG.get("ENGINE_A_CORR_CLUSTERS")
    return cfg if isinstance(cfg, dict) and cfg else CORR_CLUSTERS


def _pair_to_cluster() -> dict:
    return {
        pair: cluster
        for cluster, members in _configured_corr_clusters().items()
        for pair in (members or [])
    }


BTC_CORRELATION_MIN_BARS = 15

_BTC_BENCHMARK_PAIR = {
    "symbol": "BTCUSDT",
    "display": "BTC/USDT",
    "source": "binance",
    "type": "crypto",
}


def extract_close_prices(candles: list | None) -> list[float] | None:
    """Return chronological close prices from OHLCV candles, or None when empty."""
    if not candles:
        return None
    closes = [float(c.get("close", 0)) for c in candles if c.get("close") is not None]
    return closes or None


def pair_crypto_btc_correlation_series(
    asset_closes: list[float] | None,
    benchmark_closes: list[float] | None,
    *,
    min_bars: int = BTC_CORRELATION_MIN_BARS,
) -> tuple[list[float] | None, list[float] | None]:
    """Return both close series only when each leg meets min_bars (atomic pair)."""
    if not asset_closes or not benchmark_closes:
        return None, None
    if len(asset_closes) < min_bars or len(benchmark_closes) < min_bars:
        return None, None
    return asset_closes, benchmark_closes


def resolve_btc_h4_benchmark_candles(
    limit: int,
    *,
    config: dict,
    fetch_signal: Callable[[dict, str, int], tuple[list | None, dict | None]],
    fetch_default: Callable[[dict, str, int], list | None],
) -> tuple[list | None, str | None]:
    """Fetch BTC H4 candles for alt/BTC correlation with optional Binance fallback."""
    from athena_app.services.crypto_signal_feed import crypto_signal_feed_fallback_enabled

    try:
        candles, meta = fetch_signal(_BTC_BENCHMARK_PAIR, "H4", int(limit))
        if candles:
            upstream = (meta or {}).get("upstream") if isinstance(meta, dict) else None
            return candles, str(upstream or "signal_feed")
        allow_fallback = crypto_signal_feed_fallback_enabled("A", config) or bool(
            config.get("ENGINE_A_CRYPTO_DERIVATIVES_BINANCE_FALLBACK", False)
        )
        if allow_fallback:
            fallback = fetch_default(_BTC_BENCHMARK_PAIR, "H4", int(limit))
            if fallback:
                return fallback, "binance_futures_fallback"
    except Exception as exc:
        log.debug("[CORR] BTC benchmark fetch failed: %s", exc)
    return None, None


def compute_btc_pearson_correlation(
    asset_prices: list[float] | None,
    benchmark_prices: list[float] | None,
    *,
    pair_display: str = "",
) -> tuple[float, str]:
    """Resolve alt/BTC Pearson r and a diagnostic source label for factorDiagnostics."""
    if asset_prices is not None and benchmark_prices is not None:
        min_len = min(len(asset_prices), len(benchmark_prices))
        if min_len < BTC_CORRELATION_MIN_BARS:
            return 0.0, "neutral_insufficient_overlap"
        return (
            _get_30d_correlation(
                asset_prices=asset_prices,
                benchmark_prices=benchmark_prices,
            ),
            "pearson",
        )
    if asset_prices is not None and benchmark_prices is None:
        log.debug(
            "[CORR] %s btc_benchmark_unavailable — neutral BTC correlation",
            pair_display or "?",
        )
        return 0.0, "neutral_benchmark_missing"
    if CONFIG.get("ENGINE_A_BTC_CORR_WARN_ON_MISSING_SERIES", False):
        log.warning(
            "no price series for %s correlation — returning 0.0 (neutral, no BTC bias). "
            "Pass asset_prices + benchmark_prices for real Pearson r.",
            pair_display or "unknown",
        )
    else:
        log.debug(
            "[CORR] %s no H4 price series for BTC correlation — neutral",
            pair_display or "?",
        )
    return 0.0, "neutral_missing_series"


def _get_30d_correlation(
    asset_prices: Optional[List[float]] = None,
    benchmark_prices: Optional[List[float]] = None,
    *,
    pair_display: str = "",
    btc_symbol: str = "BTCUSDT",
) -> float:
    """Return 30-day Pearson correlation between asset and benchmark price series.

    Args:
        asset_prices:   List/array of asset close prices (chronological order).
        benchmark_prices: List/array of benchmark close prices (same length).
        pair_display:   Legacy fallback label — used only when no price series
                        are provided (returns hardcoded heuristic values).
        btc_symbol:     Legacy parameter — ignored when real prices are passed.

    Returns:
        Pearson r in range [-1, 1].  0.0 on insufficient data or zero variance.
    """
    # ── Real calculation path (preferred) ────────────────────────────────────
    if asset_prices is not None and benchmark_prices is not None:
        n = len(asset_prices)
        if n < 15 or len(benchmark_prices) < 15:
            warnings.warn(
                f"_get_30d_correlation: insufficient data points ({n}<15), returning 0.0",
                UserWarning,
                stacklevel=2,
            )
            return 0.0

        # Try numpy first (fast vectorised path)
        try:
            import numpy as np

            a = np.asarray(asset_prices, dtype=float)
            b = np.asarray(benchmark_prices, dtype=float)
            # Use minimum length if mismatched
            min_len = min(len(a), len(b))
            a = a[-min_len:]
            b = b[-min_len:]
            if min_len < 15:
                warnings.warn(
                    f"_get_30d_correlation: insufficient overlap ({min_len}<15), returning 0.0",
                    UserWarning,
                    stacklevel=2,
                )
                return 0.0
            a_mean = np.mean(a)
            b_mean = np.mean(b)
            a_std = np.std(a, ddof=0)
            b_std = np.std(b, ddof=0)
            if a_std == 0.0 or b_std == 0.0:
                return 0.0
            cov = np.mean((a - a_mean) * (b - b_mean))
            r = cov / (a_std * b_std)
            return float(max(-1.0, min(1.0, r)))
        except ImportError:
            pass  # fall through to pure-Python

        # Pure-Python Pearson (no numpy)
        min_len = min(len(asset_prices), len(benchmark_prices))
        a = [float(x) for x in asset_prices[-min_len:]]
        b = [float(x) for x in benchmark_prices[-min_len:]]
        if min_len < 15:
            warnings.warn(
                f"_get_30d_correlation: insufficient overlap ({min_len}<15), returning 0.0",
                UserWarning,
                stacklevel=2,
            )
            return 0.0
        a_mean = sum(a) / min_len
        b_mean = sum(b) / min_len
        a_var = sum((x - a_mean) ** 2 for x in a) / min_len
        b_var = sum((x - b_mean) ** 2 for x in b) / min_len
        if a_var == 0.0 or b_var == 0.0:
            return 0.0
        cov = sum((a[i] - a_mean) * (b[i] - b_mean) for i in range(min_len)) / min_len
        denom = (a_var * b_var) ** 0.5
        r = cov / denom
        return max(-1.0, min(1.0, r))

    # ── No price data: neutral correlation (no BTC bias adjustment) ───────────
    corr, _source = compute_btc_pearson_correlation(None, None, pair_display=pair_display)
    return corr


def apply_correlation_cap(signals: list) -> list:
    """Tag signals with correlationWarning if cluster already has 2+ active signals."""
    cluster_counts: dict = {}
    pair_to_cluster = _pair_to_cluster()
    for sig in signals:
        pair_name = sig["pair"]
        cluster = pair_to_cluster.get(pair_name)
        if cluster is not None:
            cluster_counts[cluster] = cluster_counts.get(cluster, 0) + 1
            if cluster_counts[cluster] >= 2:
                sig.setdefault("warnings", [])
                sig["warnings"].append(
                    f"CORR CAP: {cluster} cluster already has {cluster_counts[cluster] - 1}"
                    f" signal(s) — halve size to cap USD exposure"
                )
                sig["correlationWarning"] = cluster
    return signals


def calc_confluence(
    d1: dict,
    h4: dict,
    h1: dict,
    vr: float,
    stoch: dict,
    pair: dict,
    btc_bias: str,
    d1_candles: list | None = None,
    h4_candles: list | None = None,
    h1_candles: list | None = None,
    funding_rate: float | None = None,
    volume_threshold: float | None = None,
    bar_time: str | None = None,
    regime_context: dict | None = None,
    oi_data: dict | None = None,
    oi_context: dict | None = None,
    macro_context: dict | None = None,
    intermarket_context: dict | None = None,
    structure_result: dict | None = None,
    asset_prices: list | None = None,
    benchmark_prices: list | None = None,
    btc_benchmark_feed: str | None = None,
    style: str | None = None,
) -> dict:
    """Factor-based confluence using normalized indicators, regime-aware weights, and correlation filtering.
    Preserves legacy API and raw-threshold warnings for human readability.
    """
    from factor_scoring import compute_factor_scores, build_oi_context_for_factor_scoring
    from confidence_engine import compute_confidence

    _oi_ctx_eff = oi_context
    if _oi_ctx_eff is None and oi_data is not None and pair.get("type") == "crypto":
        _oi_ctx_eff = build_oi_context_for_factor_scoring(
            oi_data, d1_candles or [], (h1.get("snap") if h1 else None) or {}
        )

    # Compute factor scores
    factor_result = compute_factor_scores(
        d1["snap"],
        h4["snap"],
        h1["snap"],
        pair,
        d1_candles or [],
        h4_candles or [],
        h1_candles or [],
        vr,
        funding_rate,
        bar_time,
        regime_context,
        oi_context=_oi_ctx_eff,
        macro_context=macro_context,
        intermarket_context=intermarket_context,
        structure_result=structure_result,
        volume_threshold=volume_threshold,
        style=style,
    )

    # FIX 2: BTC Bias Conditional on Correlation
    _btc_mult = 1.0
    _btc_corr_diag: float | None = None
    _btc_corr_source: str | None = None
    _btc_benchmark_feed_diag = btc_benchmark_feed
    _dir = factor_result.get("direction")
    _pair_display = pair.get("display", "")
    if pair.get("type") == "crypto" and btc_bias and btc_bias != "neutral" and _dir is not None:
        if "BTC" not in _pair_display:
            # Only apply BTC bias if altcoin actually correlates with BTC
            btc_corr, _btc_corr_source = compute_btc_pearson_correlation(
                asset_prices,
                benchmark_prices,
                pair_display=_pair_display,
            )
            _btc_corr_diag = btc_corr
            if btc_corr > 0.80:
                # High correlation: BTC bias matters
                if (btc_bias == "bullish" and _dir == "LONG") or \
                   (btc_bias == "bearish" and _dir == "SHORT"):
                    _btc_mult = 1.05
                else:
                    _btc_mult = 0.90
            elif btc_corr < 0.50:
                # Low correlation: BTC bias irrelevant
                _btc_mult = 1.0
            else:
                # Moderate correlation: mild effect
                if (btc_bias == "bullish" and _dir == "LONG") or \
                   (btc_bias == "bearish" and _dir == "SHORT"):
                    _btc_mult = 1.03
                else:
                    _btc_mult = 0.95

    _fs = factor_result.get("final_score", 0.0)
    if _btc_mult != 1.0:
        factor_result = dict(factor_result)
        _adjusted = round(min(3.0, _fs * _btc_mult), 4)
        factor_result["final_score"] = _adjusted
        factor_result["btc_bias_applied"] = _btc_mult
        # btc_bias_delta lets Marcus Reid and UI show the raw vs adjusted difference
        # without conflating it with factor math (directional_score stays pre-BTC).
        factor_result["btc_bias_adjusted_score"] = _adjusted
        factor_result["btc_bias_delta"] = round(_adjusted - _fs, 4)

    # Preserve warnings for readability using the same score_group-aware bounds as factor scoring.
    w = []
    s4 = h4["snap"]
    _ptype = pair["type"]
    _rsi_b = _resolve_class_keyed(
        CONFIG.get("RSI_BOUNDS", {}), get_pair_score_group(pair), _ptype, {"ob": 70, "os": 30}
    )
    if not isinstance(_rsi_b, dict):
        _rsi_b = {"ob": 70, "os": 30}
    r4 = s4.get("rsi")
    if r4 is not None:
        if r4 >= _rsi_b["ob"]:
            w.append(
                f"H4 RSI overbought ({r4:.0f} >= {_rsi_b['ob']}) — wait for pullback"
            )
        elif r4 <= _rsi_b["os"]:
            w.append(f"H4 RSI oversold ({r4:.0f} <= {_rsi_b['os']}) — wait for bounce")
    _fs_feed_early = factor_result.get("feed_status") or {}
    if _fs_feed_early.get("adx") in {"missing", "missing_both_abort"}:
        w.append(
            "ADX unavailable on both D1 and H4 — scoring aborted (ADX_MISSING_BOTH_ABORT=true)."
        )
    # Crypto OI divergence (parity with analyze_pair — uses D1[-2] vs H1 close)
    if oi_data is not None and pair.get("type") == "crypto":
        from data_feeds import _calc_oi_divergence

        d1c = d1_candles or []
        h1_snap = h1.get("snap") if h1 else {}
        _prev_close = None
        if len(d1c) >= 2:
            try:
                _prev_close = float(d1c[-2]["close"])
            except (TypeError, ValueError):
                _prev_close = None
        _cur_close = h1_snap.get("close") if h1_snap else None
        if _cur_close is None and d1c:
            try:
                _cur_close = float(d1c[-1]["close"])
            except (TypeError, ValueError):
                _cur_close = None
        if _prev_close is not None and _cur_close is not None:
            div = _calc_oi_divergence(oi_data, float(_cur_close), _prev_close)
            if div and div.get("warning"):
                w.append(div["warning"])
    # Map final_score to legacy 'score' field
    score = float(factor_result.get("final_score") or 0)
    direction = factor_result.get("direction") or "neutral"
    log.debug(
        f"[FACTOR] {pair.get('display')} score={score:.3f} dir={direction} "
        f"dir_score={float(factor_result.get('directional_score', 0) or 0):.3f} "
        f"nondir_score={float(factor_result.get('nondirectional_score', 0) or 0):.3f} "
        f"factors={factor_result['factor_scores']} regime={factor_result['regime']}"
    )
    # Legacy compatibility: construct votes dict from factor scores
    v = {}
    for factor, val in factor_result["factor_scores"].items():
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            v[f"FACTOR_{factor.upper()}"] = _vote_sign(val)

    # Map legacy vote names for UI compatibility. The previous "D1 ADX Trend"
    # label was misleading — it pulled from FACTOR_MOMENTUM, which is a
    # momentum/quality factor, not an ADX trend factor. Renamed to
    # "D1 Momentum" so audit / Marcus narrate the actual evidence source.
    legacy_votes = {
        label: v.get(f"FACTOR_{factor.upper()}", 0)
        for label, factor in _LEGACY_VOTE_FACTOR_MAP.items()
    }
    # Update votes dict with legacy names (remove FACTOR_ prefix)
    v.update(legacy_votes)

    # Use factor result; preserve legacy return structure
    score = round(score, 2)
    trend_state, trend_state_adx_source, adx_val = _resolve_trend_state(
        factor_result, s4, pair["type"]
    )
    # Legacy compatibility values
    bull = max(0.0, score) if direction != "SHORT" else 0.0
    bear = max(0.0, score) if direction == "SHORT" else 0.0
    # Spread = abs directional score on the engine-native scale: factor engine v2 runs 0-3.0.
    spread = round(abs(factor_result.get("directional_score", 0.0)), 2)
    # Rebuild regime as dict so callers can do res['regime'].get('state')
    _regime_str = factor_result.get("regime", "UNKNOWN") or "UNKNOWN"
    _REGIME_STATE = {
        "TRENDING": 0,
        "RANGING": 1,
        "HIGH_VOLATILITY": 2,
        "LOW_VOLATILITY": 3,
    }
    _regime = {"state": _REGIME_STATE.get(_regime_str.upper(), 1), "label": _regime_str}
    # Regime is informational — do not append to warnings so the signal warnings
    # list stays signal-quality only (consumed by Marcus Reid AI prompt).
    # Confidence engine — diagnostic field (graceful degradation)
    # Derive signal classification from factor result flags
    _squeeze_bonus = factor_result.get("squeeze_bonus", False)
    _atr_breakout = factor_result.get("atr_breakout", False)
    _signal_class = classify_signal_setup(
        direction, "trend", squeeze_bonus=_squeeze_bonus,
        atr_breakout=_atr_breakout, votes=v,
    )
    _SIGNAL_TYPE_MAP = {
        "mean_reversion": "mean_reversion",
        "breakout": "breakout",
        "trend_pullback": "trend",
        "trend_continuation": "trend",
    }
    _signal_type = _SIGNAL_TYPE_MAP.get(_signal_class, "trend")
    _d1_proxy = _tf_score_proxy(d1["snap"])
    _h4_proxy = _tf_score_proxy(h4["snap"])
    _h1_proxy = _tf_score_proxy(h1["snap"])
    _hard_abort = (
        factor_result.get("signalExecutable") is False
        or factor_result.get("directionStatus") == "diagnostic_only"
        or factor_result.get("abort_reason") in {
            "atr_invalid_abort",
            "indeterminate_trend",
            "min_directional_failed",
            "adx_hard_abort",
            "adx_missing_both_abort",
            "final_score_invalid",
            "weighted_tf_tie",
        }
    )
    _pair_type = pair.get("type", "forex")
    _pair_label = pair.get("display", "") or pair.get("symbol", "")
    _session_quality = get_session(
        bar_time,
        asset_class=_pair_type,
        symbol=_pair_label,
    )["quality"]
    if _hard_abort:
        _conf = {
            "confidence": 0.0,
            "components": {},
            "available_count": 0,
            "degraded": True,
            "reason": factor_result.get("abort_reason") or "engine_a_hard_abort",
            "session_quality": _session_quality,
        }
    else:
        _conf = compute_confidence(
            factor_result=factor_result,
            d1_factor_result=_d1_proxy,
            h4_factor_result=_h4_proxy,
            h1_factor_result=_h1_proxy,
            signal_type=_signal_type,
            volume_ratio=vr,
            session_quality=_session_quality,
        )
    confidence_val = _conf["confidence"]
    # Report-only multiplier flip telemetry (threshold known only here).
    _mult_attr = annotate_multiplier_flips(
        factor_result.get("multiplier_attribution") or {},
        factor_result.get("final_score"),
        get_score_threshold(pair),
    )
    _crypto_diag = factor_result.get("crypto_engine_a_diagnostics") or {}
    if _crypto_diag.get("late_trend_adjustment_applied"):
        _lt_conf_mult = float(CONFIG.get("ENGINE_A_CRYPTO_LATE_TREND_CONF_MULT", 0.85))
        confidence_val = round(confidence_val * _lt_conf_mult, 4)
        _conf["confidence"] = confidence_val
        _conf["late_trend_confidence_mult"] = _lt_conf_mult
    # Legacy return dict
    result = {
        "score": score,
        "votes": v,
        "direction": direction,
        "bull": round(bull, 1),
        "bear": round(bear, 1),
        "spread": spread,
        "warnings": w,
        "trendState": trend_state,
        "weinsteinStage": None,
        "weinsteinLabel": None,
        "entryMode": _signal_type,
        "adxMomentum": None,
        "adxSlope": None,
        "signalClass": _signal_class,
        "regime": _regime,
        "fundingRate": funding_rate,
        "maxScoreOverride": 3.0,  # Engine A v2 score cap (trend × adx × session × conviction)
        "regimeNote": f"Regime: {_regime_str.upper()}",
        # New fields for factor diagnostics (Unified to snake_case)
        "factor_scores": factor_result["factor_scores"],
        "factor_weights": factor_result["weights"],
        "regimeName": factor_result["regime"],
        # Confidence diagnostics
        "confidence": confidence_val,
        "confidenceDetail": _conf,
        # Factor diagnostics
        "correlationAdjustments": factor_result.get("correlation_adjustments", {}),
        "disabledFactors": factor_result.get("disabled_factors", []),
        "factorDiagnostics": {
            "directionalScore": factor_result.get("directional_score"),
            "nondirectionalScore": factor_result.get("nondirectional_score"),
            "directionalConfidenceMultiplier": factor_result.get("directional_confidence_multiplier"),
            "minDirectionalThreshold": factor_result.get("min_directional_threshold"),
            "effectiveMinDirectional": factor_result.get("effective_min_directional"),
            "minDirectionalFailed": factor_result.get("min_directional_failed", False),
            "activeDirectionalFactors": factor_result.get("active_directional_factors", []),
            "activeNondirectionalFactors": factor_result.get("active_nondirectional_factors", []),
            "trendCoherence": factor_result.get("trend_coherence", {}),
            "missingDirectionalOptionalCount": factor_result.get("missing_directional_optional_count"),
            "optionalFactorCoverage": factor_result.get("optional_factor_coverage"),
            "feedStatus": factor_result.get("feed_status", {}),
            "addon_type": factor_result.get("addon_type"),
            "addon_value": factor_result.get("addon_value"),
            "addon_unsupported": factor_result.get("addon_unsupported"),
            "orthoEnabled": factor_result.get("orthoEnabled", False),
            "orthoVote": factor_result.get("orthoVote"),
            "momentumQuality": factor_result.get("momentum_quality"),
            "adxMultiplier": factor_result.get("adx_multiplier"),
            "directionalRampMult": factor_result.get("directional_ramp_multiplier"),
            "conviction": factor_result.get("conviction"),
            "multiplierAttribution": _mult_attr,
            "correlatedPenaltyGuard": factor_result.get("correlated_penalty_guard"),
            "diAlignMult": _safe_feed_mult(factor_result.get("feed_status", {}), "di_align"),
            "regimeLabelsDualCapture": {
                "trendState": trend_state,
                "factorRegime": _regime_str,
                "trendStateAdxSource": trend_state_adx_source,
                "trendStateAdxValue": round(adx_val, 2) if adx_val is not None else None,
            },
            "macroContext": macro_context or {},
            "intermarket": factor_result.get("intermarket_confirmation") or {},
            "intermarket_engine_a_delta": factor_result.get("intermarket_engine_a_delta", 0.0),
            "insufficientFactors": factor_result.get("insufficient_factors", False),
            "cryptoEngineADiagnostics": factor_result.get("crypto_engine_a_diagnostics"),
            "engineAAssetDiagnostics": factor_result.get("engine_a_asset_diagnostics"),
            "researchLabValue": factor_result.get("research_lab_value"),
            "researchLabDetail": factor_result.get("research_lab_detail"),
            "researchLabScoreUplift": factor_result.get("research_lab_score_uplift"),
            "engineACorrelatedOverlayGuard": factor_result.get("engine_a_correlated_overlay_guard"),
            "signalStatus": factor_result.get("signal_status"),
            "factorBreakdown": factor_result.get("factor_breakdown", {}),
            "abortReason": factor_result.get("abort_reason"),
            "engineVersion": "A_V2",
            "scorerSelected": "factor_scoring.compute_factor_scores",
            "scoringProfile": factor_result.get("scoring_profile"),
            "scoringStyle": factor_result.get("scoring_style"),
            "momentumTimeframe": factor_result.get("momentum_timeframe"),
            "regimeTimeframe": factor_result.get("regime_timeframe"),
            "btcCorrelation": _btc_corr_diag,
            "btcCorrelationSource": _btc_corr_source,
            "btcBenchmarkFeed": _btc_benchmark_feed_diag,
        },
        "intermarketConfirmation": factor_result.get("intermarket_confirmation") or {},
    }

    try:
        from calibration_diagnostics import (
            build_engine_a_calibration_row,
            record_calibration_diagnostic,
        )

        _threshold_regime = engine_a_regime_label_for_threshold(result)
        _threshold = get_score_threshold(pair, regime=_threshold_regime)
        _passed = bool(score >= _threshold and direction in ("LONG", "SHORT"))
        _failure = None if _passed else factor_result.get("abort_reason") or "below_engine_a_threshold"
        record_calibration_diagnostic(
            build_engine_a_calibration_row(
                pair=pair,
                result=result,
                factor_result=factor_result,
                threshold=_threshold,
                passed=_passed,
                failure_reason=_failure,
            )
        )
    except Exception as exc:
        log.warning("[CALIBRATION-DIAG] Engine A diagnostic skipped: %s", exc)
    return result


# ── Scan classification helpers — pure functions, no Flask/athena deps ────────
# Kept here so unit tests can import without touching athena.py (CLAUDE.md Rule 3).


def _pair_exchange_code(pair: dict) -> str | None:
    """Return the exchange code for a pair (JSE / US) or None."""
    sym = pair.get("symbol", "")
    if ".JO" in sym:
        return "JSE"
    if (".US" in sym or ".INDX" in sym) and pair.get("type") in ("stock", "index"):
        return "US"
    return None


def _pair_exchange_closed(pair: dict, closed_exchanges: set) -> bool:
    exch = _pair_exchange_code(pair)
    return exch in closed_exchanges if exch else False


def _build_event_risk(
    pair: dict, ds_ctx: dict, earnings_ctx: dict, closed_exchanges: set
) -> dict:
    """Build event-risk dict (hardBlock + reasons) for a pair."""
    risk: dict = {"hardBlock": False, "reasons": []}
    sym = pair.get("symbol", "")
    if _pair_exchange_closed(pair, closed_exchanges):
        risk["exchangeClosed"] = True
        risk["reasons"].append("Exchange closed")
    if sym in earnings_ctx:
        e = earnings_ctx[sym]
        risk["earnings"] = e
        risk["reasons"].append(f"Earnings in {e['daysTo']} day(s)")
        if e.get("daysTo", 99) <= 1:
            risk["hardBlock"] = True
    if sym in ds_ctx:
        ev = ds_ctx[sym]
        if ev.get("upcomingDiv"):
            d = ev["upcomingDiv"][0]
            risk["dividend"] = d
            risk["reasons"].append(f"Ex-div in {d['daysTo']} day(s)")
        if ev.get("upcomingSplit"):
            s = ev["upcomingSplit"][0]
            risk["split"] = s
            risk["reasons"].append(f"Split in {s['daysTo']} day(s)")
            if s.get("daysTo", 99) <= 3:
                risk["hardBlock"] = True
    return risk


def get_blocked_trend_states(
    pair: dict | None = None,
    scope: str = "backtest",
) -> set[str]:
    """Resolve Engine A blocked trend states for a given scope.

    Research 2026-04-18 (factor_dump_intra2.jsonl, n=393 trades, 4 crypto + 4 forex,
    intraday) showed DEAD RANGING (WR 18.5%, avgR -0.59) and DEVELOPING (WR 32.7%,
    avgR -0.19) are reliably unprofitable in backtest. Live-scan tier classification
    is kept untouched by default (scope="live" returns empty unless explicitly
    configured).

    Config key: ``ENGINE_A_BLOCKED_TREND_STATES``
        - flat list:            treated as backtest-only (back-compat)
        - scope dict:           {"backtest": [...], "live": [...]}
        - scope + per-asset:    {"backtest": {"default": [...], "forex": [...]}}

    ``scope`` must be "backtest" or "live".
    """
    cfg = CONFIG.get("ENGINE_A_BLOCKED_TREND_STATES")
    if cfg is None:
        return set()

    scope_key = "live" if str(scope).lower() == "live" else "backtest"

    # Flat list => apply to backtest only (research source), leave live untouched.
    if isinstance(cfg, list):
        if scope_key == "backtest":
            return {str(s).upper() for s in cfg if s}
        return set()

    if not isinstance(cfg, dict):
        return set()

    scoped = cfg.get(scope_key)
    if scoped is None:
        return set()

    if isinstance(scoped, list):
        return {str(s).upper() for s in scoped if s}

    if isinstance(scoped, dict):
        ptype = (pair or {}).get("type", "") if isinstance(pair, dict) else ""
        values = scoped.get(ptype)
        if values is None:
            values = scoped.get("default", [])
        return {str(s).upper() for s in (values or []) if s}

    return set()


def is_trend_state_blocked(
    trend_state: str | None,
    pair: dict | None = None,
    scope: str = "backtest",
) -> bool:
    """Return True if trend_state is in ENGINE_A_BLOCKED_TREND_STATES for this scope."""
    if not trend_state:
        return False
    return str(trend_state).upper() in get_blocked_trend_states(pair, scope=scope)


def _auto_trade_min_conviction(pair: dict) -> float | None:
    cfg = CONFIG.get("AUTO_TRADE_MIN_CONVICTION", {}) or {}
    ptype = pair.get("type", "")
    raw = cfg.get(ptype, cfg.get("default")) if isinstance(cfg, dict) else cfg
    try:
        return float(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _a_only_auto_weight(pair: dict) -> float | None:
    cfg = CONFIG.get("AUTO_TRADE_A_ONLY_WEIGHT", {}) or {}
    ptype = pair.get("type", "")
    raw = cfg.get(ptype, cfg.get("default")) if isinstance(cfg, dict) else cfg
    try:
        weight = float(raw)
    except (TypeError, ValueError):
        return None
    return weight if weight > 0 else None


def _a_only_required_score(pair: dict, signal: dict) -> float | None:
    min_conviction = _auto_trade_min_conviction(pair)
    a_only_weight = _a_only_auto_weight(pair)
    if min_conviction is None or a_only_weight is None:
        return None
    try:
        max_score = float(
            signal.get("maxScore")
            or signal.get("maxScoreOverride")
            or 3.0
        )
    except (TypeError, ValueError):
        max_score = 3.0
    if max_score <= 0:
        return None
    return (min_conviction / a_only_weight) * max_score


def _signal_confidence_for_gate(signal: dict) -> float | None:
    """Resolve Engine A confidence (0-1) for trade-tier gating."""
    for key in ("confidence",):
        raw = signal.get(key)
        if raw is not None:
            try:
                return float(raw)
            except (TypeError, ValueError):
                pass
    detail = signal.get("confidenceDetail")
    if isinstance(detail, dict):
        raw = detail.get("confidence")
        if raw is not None:
            try:
                return float(raw)
            except (TypeError, ValueError):
                pass
    return None


def _classify_signal(signal: dict, pair: dict) -> tuple[str, str]:
    """Return (tier, reason) where tier is 'trade' | 'watchlist' | 'skip'."""
    if (
        str(signal.get("engine") or "").upper() == "ENGINE_A_V3"
        and str(signal.get("contractVersion") or "").startswith("3.")
    ):
        decision = str(signal.get("decision") or "NO_SIGNAL").upper()
        reasons = [str(value) for value in signal.get("rejectionReasons") or [] if value]
        reason = "; ".join(dict.fromkeys(reasons))
        if decision == "NO_SIGNAL":
            return "skip", reason or "V3 specialist returned NO_SIGNAL"
        if decision == "WATCH":
            return "watchlist", reason or "V3 specialist is awaiting confirmation"
        if decision != "TRADE" or signal.get("qualified") is not True:
            return "skip", reason or "V3 signal is not trade-qualified"
        if signal.get("engineATradeEnabled") is not True:
            return "watchlist", reason or "V3 execution eligibility is disabled"
        if not pair.get("enabled", True):
            return "watchlist", reason or "Pair is disabled"
        if signal.get("exchangeClosed"):
            return "watchlist", reason or "Exchange closed"
        return "trade", "V3 specialist trade-qualified"

    threshold = signal.get("scanThreshold", get_min_confluence_threshold(pair))
    score = signal.get("confluenceScore", 0)
    hard_event = signal.get("eventRisk", {}).get("hardBlock", False)
    macro_event_blocked = signal.get("macroEventRisk", {}).get("blocked", False)
    exchange_closed = signal.get("exchangeClosed", False)
    trend_state = signal.get("trendState")
    trend_blocked = is_trend_state_blocked(trend_state, pair, scope="live")
    # Risk Gating Parity — allow backtests to skip live blockers unless config-gated ON
    is_research = bool(
        CONFIG.get("RESEARCH_MODE", False)
        or signal.get("researchMode", False)
        or signal.get("backtestRunning", False)
    )
    
    event_blocked = False
    if macro_event_blocked:
        if is_research:
            if CONFIG.get("BACKTEST_EVENT_RISK_GATING", False):
                event_blocked = True
        else:
            event_blocked = True

    # Note: Sentiment blocking is currently checked at execution level or scanner annotation,
    # but we ensure parity here if the signal dict carries a 'sentimentBlocked' flag.
    sentiment_blocked = signal.get("sentimentBlocked", False)
    if sentiment_blocked:
        if is_research:
            if not CONFIG.get("BACKTEST_SENTIMENT_GATING", False):
                sentiment_blocked = False

    scan_ready = (
        pair.get("enabled", True)
        and not exchange_closed
        and not hard_event
        and not event_blocked
        and not sentiment_blocked
        and not trend_blocked
        and score >= threshold
    )
    if scan_ready:
        try:
            from engine_a_trade_gate import annotate_engine_a_trade_eligibility

            annotate_engine_a_trade_eligibility(signal, pair, config=CONFIG)
        except Exception as exc:
            log.debug("[ENGINE_A_TRADE_GATE] classification gate skipped: %s", exc)
            signal["engineATradeEnabled"] = False
            signal["engineATradeGate"] = {
                "enabled": False,
                "research_only": True,
                "source": "error_closed",
                "reason": "Engine A trade-eligibility gate unavailable; research-only fail-closed.",
            }
        if signal.get("engineATradeEnabled") is False:
            detail = signal.get("engineATradeGate") or {}
            return "watchlist", detail.get("reason") or "Engine A research-only"
        if signal.get("enginesAligned") is False:
            if bool(CONFIG.get("ENGINE_A_SCAN_A_ONLY_AUTO_GATE_ENABLED", False)):
                required = _a_only_required_score(pair, signal)
                try:
                    max_score = float(signal.get("maxScore") or signal.get("maxScoreOverride") or 3.0)
                except (TypeError, ValueError):
                    max_score = 3.0
                if required is not None and score < required:
                    return (
                        "watchlist",
                        f"A-only auto gate requires about {required:.2f}/{max_score:.1f}; "
                        f"score {float(score):.2f} clears scan floor {float(threshold):.2f} only",
                    )
        if bool(CONFIG.get("ENGINE_A_TRADE_MIN_CONFIDENCE_ENABLED", False)):
            min_conf = get_min_confidence_threshold(pair)
            conf = _signal_confidence_for_gate(signal)
            if conf is not None and conf < min_conf:
                score_group = get_pair_score_group(pair)
                return (
                    "watchlist",
                    f"Engine A confidence {conf:.2f} below {score_group} minimum {min_conf:.2f}",
                )
        return "trade", "Trade-ready"
    watch_floor = max(round(threshold - 0.3, 2), 0.2)
    reasons = [d["detail"] for d in signal.get("scanDiagnostics", [])]
    if score >= threshold and not pair.get("enabled", True):
        return "watchlist", "; ".join(reasons) or "Strong setup, but pair is disabled"

    # Update reasons if blocked by parity-gated logic
    if score >= threshold:
        if event_blocked:
            return "watchlist", "; ".join(reasons) or "Blocked by Macro Event Gate"
        if sentiment_blocked:
            return "watchlist", "; ".join(reasons) or "Blocked by Sentiment Gate"
        if exchange_closed or hard_event:
            return "watchlist", "; ".join(reasons) or "Blocked by exchange/event risk"
        if trend_blocked:
            return (
                "watchlist",
                "; ".join(reasons)
                or f"Blocked by trend state: {trend_state}",
            )
    if trend_state != "DEAD RANGING" and score >= watch_floor:
        return "watchlist", "; ".join(reasons) or f"Near miss ({score}/{threshold})"
    return "skip", "; ".join(reasons) or "Below discovery threshold"
