"""scoring.py — Confluence scoring engine, session detection, and divergence detection.

Depends on: config.CONFIG, indicators (calc_* functions).
"""

import logging
import warnings
from datetime import datetime, timezone
from typing import List, Optional

from config import CONFIG
from indicators import (
    calc_rsi,
)

log = logging.getLogger("athena")


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
_US_INDICES_TRACKERS = {"NASDAQ-100", "S&P 500", "Dow Jones", "SPY", "QQQ", "DIA"}
_EU_INDICES = {"DAX 40", "UK100"}
_ASIAN_INDICES = {"ASX 200", "Nikkei 225", "Hang Seng"}
_US_STOCK_CUSTOM = {
    "AAPL", "TSLA", "NVDA", "MSFT", "AMZN", "META", "GOOG", "JPM", "V", "XOM",
    "NFLX", "AMD", "CRM", "DIS", "BA", "COIN", "PYPL", "INTC", "UBER", "PLTR"
}
_ALTCOIN_MAJORS = {
    "SOL/USDT", "ADA/USDT", "AVAX/USDT", "LINK/USDT", "POL/USDT", "BNB/USDT",
    "DOT/USDT", "LTC/USDT", "SUI/USDT", "NEAR/USDT", "APT/USDT", "INJ/USDT",
    "RENDER/USDT"
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
    return f"{ptype}_other" if ptype else "unknown"


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

    If ``ENGINE_A_SCORE_GROUP_THRESHOLDS`` includes ``default``, this almost
    always satisfies ``get_score_threshold`` before ``_get_threshold_tier`` /
    ``_PAIR_OVERRIDES`` are consulted (omit ``default`` to use the 3-tier path).
    """
    display = pair.get("display", "")
    symbol = pair.get("symbol", "")
    ptype = pair.get("type", "")
    score_group = get_pair_score_group(pair)

    pair_thresholds = CONFIG.get("ENGINE_A_PAIR_THRESHOLDS", {}) or {}
    for key in (display, symbol):
        if key in pair_thresholds:
            return float(pair_thresholds[key])

    group_thresholds = CONFIG.get("ENGINE_A_SCORE_GROUP_THRESHOLDS", {}) or {}
    for key in (score_group, ptype, "default"):
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


def get_score_threshold(pair: dict, is_backtest: bool = False, regime: str | None = None) -> float:
    """Resolve score threshold.

    Replaces the old 6-class + BT_MIN_GROUP + BACKTEST_USE_BT_MIN_THRESHOLDS
    hierarchy with profile override, pair/group config, then 3-tier fallback.
    Backtest and live use same thresholds.

    When ENGINE_A_REGIME_DYNAMIC_THRESHOLDS.ENABLED is true, applies regime-based
    multipliers to the resolved threshold:
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


def get_min_confluence_threshold(pair: dict) -> float:
    """Legacy wrapper for live scan threshold resolution."""
    return get_score_threshold(pair, is_backtest=False)


def get_backtest_min_score_threshold(pair: dict) -> float:
    """Legacy wrapper for Engine A backtest gate."""
    return get_score_threshold(pair, is_backtest=True)


def pair_filter_enabled(pair: dict, filter_name: str) -> bool:
    disabled = set(get_pair_profile(pair).get("disable_filters", []) or [])
    return filter_name not in disabled


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
    """
    if entry_mode == "mean_revert":
        return "mean_reversion"
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


def get_session(bar_time: str | None = None) -> dict:
    """Determine forex session label from UTC hour (liquidity buckets for UI).

    bar_time: ISO timestamp of a specific bar (e.g. backtests). If omitted, uses
              current UTC — use this for live scans so the badge matches scan time.
    """
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
}

# Reverse lookup: pair_display → cluster_name (built once at import time)
_PAIR_TO_CLUSTER: dict = {
    pair: cluster
    for cluster, members in CORR_CLUSTERS.items()
    for pair in members
}


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

    # ── Legacy fallback path (no price data available) ───────────────────────
    log.warning(
        "heuristic BTC correlation fallback used for %s; pass price series to use real Pearson r",
        pair_display or "unknown",
    )
    # Known high-correlation majors
    if pair_display in ("ETH/USDT", "ETHUSDT"):
        return 0.90
    if pair_display in ("BTC/USDT", "BTCUSDT"):
        return 1.0
    # Known low-correlation alts
    if pair_display in ("SOL/USDT", "SOLUSDT", "DOGE/USDT", "DOGEUSDT"):
        return 0.30
    # Default: moderate correlation
    return 0.65


def apply_correlation_cap(signals: list) -> list:
    """Tag signals with correlationWarning if cluster already has 2+ active signals."""
    cluster_counts: dict = {}
    for sig in signals:
        pair_name = sig["pair"]
        cluster = _PAIR_TO_CLUSTER.get(pair_name)
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
    asset_prices: list | None = None,
    benchmark_prices: list | None = None,
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
        volume_threshold=volume_threshold,
    )

    # FIX 2: BTC Bias Conditional on Correlation
    _btc_mult = 1.0
    _dir = factor_result.get("direction")
    _pair_display = pair.get("display", "")
    if pair.get("type") == "crypto" and btc_bias and btc_bias != "neutral" and _dir is not None:
        if "BTC" not in _pair_display:
            # Only apply BTC bias if altcoin actually correlates with BTC
            # Prefer real price-series correlation; fall back to heuristic labels
            if asset_prices is not None and benchmark_prices is not None:
                btc_corr = _get_30d_correlation(
                    asset_prices=asset_prices,
                    benchmark_prices=benchmark_prices,
                )
            else:
                btc_corr = _get_30d_correlation(pair_display=_pair_display, btc_symbol="BTCUSDT")
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
    score = factor_result["final_score"]
    direction = factor_result["direction"]
    log.debug(
        f"[FACTOR] {pair.get('display')} score={score:.3f} dir={direction} "
        f"dir_score={factor_result.get('directional_score', 0):.3f} "
        f"nondir_score={factor_result.get('nondirectional_score', 0):.3f} "
        f"factors={factor_result['factor_scores']} regime={factor_result['regime']}"
    )
    # Legacy compatibility: construct votes dict from factor scores
    v = {}
    for factor, val in factor_result["factor_scores"].items():
        if val is not None:
            v[f"FACTOR_{factor.upper()}"] = _vote_sign(val)

    # Map legacy vote names for UI compatibility
    legacy_votes = {
        "D1 Trend Gate": v.get("FACTOR_TREND", 0),
        "D1 ADX Trend": v.get("FACTOR_MOMENTUM", 0),
        "D1 Weinstein Stage": v.get("FACTOR_WEINSTEIN", 0),
        "H4 MACD Momentum": v.get("FACTOR_MOMENTUM", 0),
        "H4 RSI Zone": v.get("FACTOR_RSI", 0),
        "H4 Stochastic": v.get("FACTOR_STOCH", 0),
        "H4 Fib Level": v.get("FACTOR_FIB", 0),
        "H1 EMA Entry": v.get("FACTOR_EMA", 0),
        "H1 BB Pullback": v.get("FACTOR_BB", 0),
        "H1 RSI Divergence": v.get("FACTOR_RSI_DIV", 0),
    }
    # Update votes dict with legacy names (remove FACTOR_ prefix)
    v.update(legacy_votes)

    # Use factor result; preserve legacy return structure
    score = round(score, 2)
    adx_val = s4.get("adx")
    _rng = CONFIG["RANGING"].get(pair["type"], CONFIG["RANGING"]["commodity"])
    trend_state = "UNKNOWN"
    if adx_val is not None:
        if adx_val >= CONFIG.get("TRENDING_ADX", 35):
            trend_state = "TRENDING"
        elif adx_val >= CONFIG.get("DEVELOPING_ADX", 25):
            trend_state = "DEVELOPING"
        elif adx_val >= _rng["dead"]:
            trend_state = "RANGING"
        else:
            trend_state = "DEAD RANGING"
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
    _conf = compute_confidence(
        factor_result=factor_result,
        d1_factor_result=_d1_proxy,
        h4_factor_result=_h4_proxy,
        h1_factor_result=_h1_proxy,
        signal_type=_signal_type,
        volume_ratio=vr,
        session_quality=get_session(bar_time)["quality"],
    )
    confidence_val = _conf["confidence"]
    # Legacy return dict
    return {
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
            "macroContext": macro_context or {},
            "intermarket": factor_result.get("intermarket_confirmation") or {},
            "insufficientFactors": factor_result.get("insufficient_factors", False),
            "cryptoEngineADiagnostics": factor_result.get("crypto_engine_a_diagnostics"),
            "engineAAssetDiagnostics": factor_result.get("engine_a_asset_diagnostics"),
            "researchLabValue": factor_result.get("research_lab_value"),
            "researchLabDetail": factor_result.get("research_lab_detail"),
        },
        "intermarketConfirmation": factor_result.get("intermarket_confirmation") or {},
    }


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


def _classify_signal(signal: dict, pair: dict) -> tuple[str, str]:
    """Return (tier, reason) where tier is 'trade' | 'watchlist' | 'skip'."""
    threshold = signal.get("scanThreshold", get_min_confluence_threshold(pair))
    score = signal.get("confluenceScore", 0)
    hard_event = signal.get("eventRisk", {}).get("hardBlock", False)
    macro_event_blocked = signal.get("macroEventRisk", {}).get("blocked", False)
    exchange_closed = signal.get("exchangeClosed", False)
    trend_state = signal.get("trendState")
    trend_blocked = is_trend_state_blocked(trend_state, pair, scope="live")
    # Risk Gating Parity — allow backtests to skip live blockers unless config-gated ON
    is_research = CONFIG.get("RESEARCH_MODE", False) or CONFIG.get("BACKTEST_RUNNING", False)
    
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
        if signal.get("enginesAligned") is False:
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
