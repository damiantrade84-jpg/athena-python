"""factor_scoring.py — Factor-based scoring engine with regime-aware weights and correlation filtering.

Computes factor scores from normalized indicators, applies regime weights, filters correlated indicators,
and aggregates to final signal score.

Blueprint compliance:
  - Regime detection (TRENDING/RANGING/HIGH_VOLATILITY/LOW_VOLATILITY) dynamically selects weights.
  - Indicator correlation filter: abs(corr) > 0.8 → reduce weaker indicator weight by 50% (capped).
  - Missing factors excluded from scoring (not treated as 0).
  - Raw thresholds for warnings only; scoring uses normalized z-scores/percentiles.
  - DIRECTIONAL indicators (ema_trend, rsi_z, macdLine_z, funding_rate, microstructure)
    determine trade direction and contribute signed scores.
  - NON-DIRECTIONAL indicators (adx_z, atr_z, bbWidth_z, realized_vol_z, volume_ratio,
    fib_proximity) measure signal quality/strength — contribute via abs().
"""
import math
import logging
from typing import List, Dict, Optional
from config import CONFIG
from regime import detect_regime

log = logging.getLogger("athena")


# ── Correlation filter ───────────────────────────────────────────────────────

def _pearson(x: List[float], y: List[float]) -> Optional[float]:
    """Pearson correlation between two equal-length float lists. Returns None if insufficient data."""
    pairs = [(a, b) for a, b in zip(x, y) if a is not None and b is not None]
    n = len(pairs)
    if n < 20:
        return None
    mx = sum(a for a, _ in pairs) / n
    my = sum(b for _, b in pairs) / n
    num = sum((a - mx) * (b - my) for a, b in pairs)
    dx = sum((a - mx) ** 2 for a, _ in pairs)
    dy = sum((b - my) ** 2 for _, b in pairs)
    den = math.sqrt(dx) * math.sqrt(dy)
    return num / den if den > 0 else 0.0


def _build_indicator_series(h4_candles: List[dict], window: int) -> Dict[str, List[float]]:
    """Build indicator time series from H4 candles for correlation computation.
    Only imports are lightweight — reuses the same calc functions as indicators.py.
    """
    from indicators import calc_rsi, calc_macd, calc_atr, calc_adx
    if len(h4_candles) < max(window, 50):
        return {}
    candles = h4_candles[-window:]
    closes = [c["close"] for c in candles]
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    return {
        "rsi_z": calc_rsi(closes, 14),
        "macdLine_z": calc_macd(closes)["macd"],
        "atr_z": calc_atr(highs, lows, closes, 14),
        "adx_z": calc_adx(highs, lows, closes, 14)["adx"],
    }


def _apply_correlation_filter(indicator_scalars: Dict[str, Optional[float]],
                               h4_candles: List[dict],
                               window: int) -> Dict[str, float]:
    """Per-blueprint correlation filter:
    1. Compute pairwise rolling correlation between indicator time series.
    2. If abs(correlation) > 0.8, reduce the weaker indicator's effective weight by 50%.
    3. Cap total weight reduction per indicator at 50%.

    Returns dict of indicator_name → weight_multiplier (0.5–1.0).
    Disabled when INDICATOR_CORRELATION_ENABLED=false (default) to avoid O(n²) cost in backtest.
    """
    weight_mult: Dict[str, float] = {k: 1.0 for k in indicator_scalars}
    if not CONFIG.get("INDICATOR_CORRELATION_ENABLED", False):
        return weight_mult
    series = _build_indicator_series(h4_candles, window)
    if not series:
        return weight_mult

    # Only correlate indicators we have both scalar and series for
    corr_keys = [k for k in series if k in indicator_scalars and indicator_scalars[k] is not None]
    checked = set()
    for i, k1 in enumerate(corr_keys):
        for k2 in corr_keys[i + 1:]:
            pair_key = tuple(sorted([k1, k2]))
            if pair_key in checked:
                continue
            checked.add(pair_key)
            corr = _pearson(series[k1], series[k2])
            if corr is not None and abs(corr) > 0.8:
                v1 = abs(indicator_scalars[k1]) if indicator_scalars[k1] is not None else 0.0
                v2 = abs(indicator_scalars[k2]) if indicator_scalars[k2] is not None else 0.0
                weaker = k2 if v1 >= v2 else k1
                # Reduce by 50% but cap total reduction at 50%
                weight_mult[weaker] = max(0.5, weight_mult[weaker] - 0.5)
                log.debug(f"[CORR] {k1}<->{k2} r={corr:.2f}, reducing {weaker} weight to {weight_mult[weaker]:.2f}")
    return weight_mult


# ── Factor scoring ───────────────────────────────────────────────────────────

def _weighted_factor_score(indicators: Dict[str, Optional[float]],
                           keys: List[str],
                           corr_weights: Dict[str, float],
                           use_abs: bool = False) -> Optional[float]:
    """Compute factor score as correlation-adjusted weighted mean of available indicators.
    use_abs=True for non-directional factors (always positive contribution).
    """
    vals = []
    wgts = []
    for k in keys:
        v = indicators.get(k)
        if v is not None:
            vals.append(abs(v) if use_abs else v)
            wgts.append(corr_weights.get(k, 1.0))
    if not vals:
        return None
    w_sum = sum(wgts)
    if w_sum <= 0:
        return None
    return sum(v * w / w_sum for v, w in zip(vals, wgts))


def compute_factor_scores(d1_snap: Dict, h4_snap: Dict, h1_snap: Dict, pair: Dict,
                          d1_candles: List, h4_candles: List, h1_candles: List,
                          volume_ratio: float, funding_rate: Optional[float] = None,
                          bar_time: Optional[str] = None) -> Dict:
    """Compute factor scores, apply regime weights, and aggregate to final score.

    Returns dict with final_score (always >= 0), direction, factor_scores, weights, regime, etc.
    """
    asset_type = pair.get("type", "stock")

    # ── Data quality guard ───────────────────────────────────────────────
    # Detect near-zero prices or zero ATR that indicate a data feed issue
    _close = h4_snap.get("close")
    _ema21 = h1_snap.get("ema21")
    _atr_raw = h4_snap.get("atr")
    if _close is not None and _close > 0 and _ema21 is not None:
        if _ema21 > 0 and _ema21 / _close < 0.001:
            log.warning(f"[FACTOR] {pair.get('display')} DATA QUALITY: EMA21={_ema21:.6f} vs close={_close:.4f} "
                        f"— ratio {_ema21/_close:.6f} suggests stale/corrupt data")
    if _close is not None and _close > 0 and _atr_raw is not None and _atr_raw == 0:
        log.warning(f"[FACTOR] {pair.get('display')} DATA QUALITY: ATR=0 with close={_close:.6f} "
                    f"— zero ATR indicates frozen/stale candle data")

    # ── Gather indicators ────────────────────────────────────────────────
    indicators: Dict[str, Optional[float]] = {}

    # Trend direction — EMA crossover (directional: +1/-1)
    ema21 = h1_snap.get("ema21")
    ema50 = h1_snap.get("ema50")
    if ema21 is not None and ema50 is not None and ema50 != 0:
        indicators["ema_trend"] = 1.0 if ema21 > ema50 else -1.0
    else:
        indicators["ema_trend"] = None

    # Trend strength — ADX z-score (NON-directional: high = strong trend)
    indicators["adx_z"] = h4_snap.get("adx_z")

    # Momentum (directional: positive z = bullish, negative = bearish)
    indicators["rsi_z"] = h4_snap.get("rsi_z")
    indicators["macdLine_z"] = h4_snap.get("macdLine_z")

    # Volatility (non-directional: abs value = signal quality)
    indicators["atr_z"] = h4_snap.get("atr_z")
    indicators["bbWidth_z"] = h4_snap.get("bbWidth_z")
    indicators["realized_vol_z"] = h4_snap.get("realized_vol_z")

    # Volume — forex/pairs with no reliable volume get None (not 0)
    if volume_ratio is not None and volume_ratio > 0:
        # Check if we actually have real volume data (not all zeros)
        _has_volume = any(c.get("vol", 0) > 0 for c in (h4_candles[-5:] if h4_candles else []))
        if _has_volume:
            # Center around 1.0 (average), scale: 2x average → +3.0
            indicators["volume_ratio"] = max(-3.0, min(3.0, (volume_ratio - 1.0) * 3.0))
        else:
            indicators["volume_ratio"] = None  # No real volume data — exclude factor
    else:
        indicators["volume_ratio"] = None
    obv_raw = h4_snap.get("obv_trend")
    indicators["obv_trend"] = obv_raw if obv_raw is not None else None

    # Structure — fib proximity (non-directional)
    fib_prox = h4_snap.get("fib_proximity")
    indicators["fib_proximity"] = fib_prox if fib_prox is not None else None

    # Derivatives — funding rate (directional: negative funding = bullish for longs)
    if funding_rate is not None and funding_rate != 0:
        # Scale to z-score range: 0.01% funding → ±0.3, 0.1% → ±3.0
        indicators["funding_rate"] = max(-3.0, min(3.0, -funding_rate * 3000))
    else:
        indicators["funding_rate"] = None  # No funding data — exclude

    # Microstructure (directional if available)
    indicators["order_book_imbalance"] = h4_snap.get("order_book_imbalance")
    indicators["liquidity_wall_detection"] = h4_snap.get("liquidity_wall_detection")
    indicators["orderflow_delta"] = h4_snap.get("orderflow_delta")
    indicators["liquidity_pressure"] = h4_snap.get("liquidity_pressure")

    # ── Correlation filter (blueprint: abs(corr) > 0.8 → reduce weaker by 50%) ──
    corr_window = CONFIG.get("INDICATOR_CORRELATION_WINDOW", 200)
    corr_weights = _apply_correlation_filter(indicators, h4_candles, corr_window)

    # ── Factor mappings ──────────────────────────────────────────────────
    # Directional factors: sign matters (positive = bullish)
    directional_factors = {
        "trend":          ["ema_trend"],
        "momentum":       ["rsi_z", "macdLine_z"],
        "derivatives":    ["funding_rate"],
        "microstructure": ["order_book_imbalance", "liquidity_wall_detection",
                           "orderflow_delta", "liquidity_pressure"],
    }
    # Non-directional factors: abs value = quality/strength (always positive)
    nondirectional_factors = {
        "trend_strength": ["adx_z"],
        "volatility":     ["atr_z", "bbWidth_z", "realized_vol_z"],
        "volume":         ["volume_ratio", "obv_trend"],
        "structure":      ["fib_proximity"],
    }

    # ── Compute factor scores (correlation-adjusted) ─────────────────────
    factor_scores: Dict[str, Optional[float]] = {}
    for factor, keys in directional_factors.items():
        factor_scores[factor] = _weighted_factor_score(indicators, keys, corr_weights, use_abs=False)
    for factor, keys in nondirectional_factors.items():
        factor_scores[factor] = _weighted_factor_score(indicators, keys, corr_weights, use_abs=True)

    # ── Determine direction from directional factors ─────────────────────
    dir_signals = []
    for factor in directional_factors:
        s = factor_scores.get(factor)
        if s is not None:
            dir_signals.append(s)
    dir_sum = sum(dir_signals) if dir_signals else 0.0
    direction = "LONG" if dir_sum >= 0 else "SHORT"

    # ── Regime detection ─────────────────────────────────────────────────
    _bbw_pct = h4_snap.get("bbWidth_pct") or h4_snap.get("bb_width_pct")
    regime = detect_regime(h4_snap, asset_type, bb_width_pct=_bbw_pct).get("regime", "UNKNOWN")
    regime_weights = CONFIG.get("REGIME_WEIGHTS", {}).get(regime.upper(), {})
    base_weights = CONFIG.get("FACTOR_WEIGHTS", {}).get(asset_type, {})

    # Map factor names to config weight keys
    _weight_key_map = {
        "trend": "trend", "momentum": "momentum", "derivatives": "derivatives",
        "microstructure": "microstructure", "trend_strength": "trend",
        "volatility": "volatility", "volume": "volume", "structure": "structure",
    }
    weights: Dict[str, float] = {}
    all_factors = dict(directional_factors)
    all_factors.update(nondirectional_factors)
    for factor in all_factors:
        wk = _weight_key_map.get(factor, factor)
        base_w = base_weights.get(wk, 1.0)
        # If base weight is 0 (asset class disables this factor), regime cannot override
        weights[factor] = 0.0 if base_w == 0 else regime_weights.get(wk, base_w)

    # ── Final aggregation ────────────────────────────────────────────────
    active_dir = {f: s for f, s in factor_scores.items() if s is not None and f in directional_factors}
    active_nondir = {f: s for f, s in factor_scores.items() if s is not None and f in nondirectional_factors}
    disabled_factors = [f for f, s in factor_scores.items() if s is None]

    # Minimum active factors guard: require at least 1 directional factor.
    # Prevents inflated scores driven solely by volatility (e.g. JSE stocks with no H4/H1 data).
    if not active_dir:
        log.debug(f"[FACTOR] {pair.get('display')} no active directional factors — score=0")
        return {
            "final_score": 0.0, "direction": "LONG", "factor_scores": factor_scores,
            "weights": weights, "regime": regime, "filtered_indicators": indicators,
            "disabled_factors": disabled_factors, "directional_score": 0.0,
            "nondirectional_score": 0.0, "correlation_adjustments": {},
        }

    dir_score = 0.0
    if active_dir:
        # Exclude factors with 0 weight
        active_dir = {f: s for f, s in active_dir.items() if weights.get(f, 1.0) > 0}
    if active_dir:
        dir_w_sum = sum(weights.get(f, 1.0) for f in active_dir)
        for f, s in active_dir.items():
            dir_score += (weights.get(f, 1.0) / dir_w_sum) * s

    nondir_score = 0.0
    if active_nondir:
        # Exclude factors with 0 weight
        active_nondir = {f: s for f, s in active_nondir.items() if weights.get(f, 1.0) > 0}
    if active_nondir:
        nondir_w_sum = sum(weights.get(f, 1.0) for f in active_nondir)
        for f, s in active_nondir.items():
            nondir_score += (weights.get(f, 1.0) / nondir_w_sum) * s

    # Combine: directional strength + quality boost (0.6 dir + 0.4 nondir)
    final_score = abs(dir_score) * 0.6 + nondir_score * 0.4

    return {
        "final_score": final_score,
        "direction": direction,
        "factor_scores": factor_scores,
        "weights": weights,
        "regime": regime,
        "filtered_indicators": indicators,
        "disabled_factors": disabled_factors,
        "directional_score": dir_score,
        "nondirectional_score": nondir_score,
        "correlation_adjustments": {k: v for k, v in corr_weights.items() if v < 1.0},
    }
