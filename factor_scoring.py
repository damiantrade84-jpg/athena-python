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
import threading
from typing import List, Dict, Optional
from config import CONFIG
from regime import detect_regime

log = logging.getLogger("athena")


def _pair_profile(pair: dict) -> dict:
    profiles = CONFIG.get("PAIR_PROFILES", {}) or {}
    return profiles.get(pair.get("display")) or profiles.get(pair.get("symbol")) or {}


def _legacy_vote_to_factor(vote_key: str) -> str | None:
    mapping = {
        "d1_trend": "trend",
        "h1_ema": "trend",
        "d1_adx": "trend_strength",
        "h4_macd": "momentum",
        "h4_oscillator": "momentum",
        "volume": "volume",
        "funding": "derivatives",
        "h4_fib": "structure",
        "h1_bb": "volatility",
        "divergence": "momentum",
    }
    return mapping.get(str(vote_key).strip().lower())


def _apply_pair_profile_weight_rules(pair: dict, weights: dict) -> dict:
    profile = _pair_profile(pair)
    out = dict(weights)

    # Disabled legacy votes become zero-weight factor groups.
    for vote in set(profile.get("disabled_votes", []) or []):
        factor = _legacy_vote_to_factor(vote)
        if factor and factor in out:
            out[factor] = 0.0

    # Legacy overrides are interpreted as multipliers on factor-group weights.
    for vote, mult in (profile.get("weight_overrides", {}) or {}).items():
        factor = _legacy_vote_to_factor(vote)
        if not factor or factor not in out:
            continue
        try:
            out[factor] = max(0.0, float(out[factor]) * float(mult))
        except (TypeError, ValueError):
            continue

    # Optional direct factor-group multipliers by score subgroup.
    score_group = pair.get("score_group")
    if score_group:
        group_mults = (
            (CONFIG.get("FACTOR_SCORE_GROUP_MULTIPLIERS", {}) or {}).get(score_group, {})
            or {}
        )
        for factor, mult in group_mults.items():
            if factor not in out:
                continue
            try:
                out[factor] = max(0.0, float(out[factor]) * float(mult))
            except (TypeError, ValueError):
                continue

    return out


# ── Regime smoothing state ────────────────────────────────────────────────────
# Tracks the last N raw regime labels per pair to prevent whipsawing.
# Only updates the committed regime once N consecutive bars agree on the new label.
_regime_history: Dict[
    str, list
] = {}  # pair_display -> rolling list of raw regime labels
_regime_committed: Dict[
    str, str
] = {}  # pair_display -> currently committed (smoothed) regime
_regime_lock = threading.Lock()


def _get_smoothed_regime(pair_id: str, raw_regime: str) -> str:
    """Return smoothed regime: only switch if new regime holds for REGIME_SMOOTHING_BARS bars."""
    min_bars = CONFIG.get("REGIME_SMOOTHING_BARS", 3)
    with _regime_lock:
        hist = _regime_history.setdefault(pair_id, [])
        committed = _regime_committed.get(pair_id)
        hist.append(raw_regime)
        # Keep only the last min_bars entries
        if len(hist) > min_bars:
            hist[:] = hist[-min_bars:]
        # Bootstrap: first observation commits immediately
        if committed is None:
            _regime_committed[pair_id] = raw_regime
            return raw_regime
        # Switch only when all of the last min_bars bars agree on the new regime
        if len(hist) >= min_bars and len(set(hist[-min_bars:])) == 1:
            _regime_committed[pair_id] = hist[-1]
        return _regime_committed[pair_id]


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


def _build_indicator_series(
    h4_candles: List[dict], window: int
) -> Dict[str, List[float]]:
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


def _apply_correlation_filter(
    indicator_scalars: Dict[str, Optional[float]], h4_candles: List[dict], window: int
) -> Dict[str, float]:
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
    corr_keys = [
        k for k in series if k in indicator_scalars and indicator_scalars[k] is not None
    ]
    checked = set()
    for i, k1 in enumerate(corr_keys):
        for k2 in corr_keys[i + 1 :]:
            pair_key = tuple(sorted([k1, k2]))
            if pair_key in checked:
                continue
            checked.add(pair_key)
            corr_raw = _pearson(series[k1], series[k2])

            if corr_raw is not None:
                corr = corr_raw

                if abs(corr) > 0.8:
                    v1 = (
                        abs(indicator_scalars[k1])
                        if indicator_scalars[k1] is not None
                        else 0.0
                    )
                    v2 = (
                        abs(indicator_scalars[k2])
                        if indicator_scalars[k2] is not None
                        else 0.0
                    )
                    weaker = k2 if v1 >= v2 else k1
                    # Reduce by 50% but cap total reduction at 50%
                    weight_mult[weaker] = max(0.5, weight_mult[weaker] - 0.5)
                    log.debug(
                        f"[CORR] {k1}<->{k2} r={corr:.2f}, reducing {weaker} weight to {weight_mult[weaker]:.2f}"
                    )
    return weight_mult


# ── Factor scoring ───────────────────────────────────────────────────────────


def _candle_microstructure(h4_candles: List[dict]) -> Dict[str, Optional[float]]:
    """Compute microstructure proxies from H4 OHLC data (no order book required).

    Works for all asset types. Crypto WS data takes priority when available.

    Returns:
        order_book_imbalance  — avg directional bar body ratio over 10 bars (signed)
        orderflow_delta       — bull/bear bar count imbalance over 10 bars (signed)
        liquidity_pressure    — avg close position within bar over 5 bars (signed)
    """
    if not h4_candles or len(h4_candles) < 5:
        return {
            "order_book_imbalance": None,
            "orderflow_delta": None,
            "liquidity_pressure": None,
        }

    recent10 = h4_candles[-10:]
    recent5 = h4_candles[-5:]

    # order_book_imbalance: mean of (close-open)/(high-low) per bar → directional body strength
    obi_vals = []
    for c in recent10:
        rng = c["high"] - c["low"]
        if rng > 0:
            obi_vals.append((c["close"] - c["open"]) / rng)
    obi = (
        max(-3.0, min(3.0, (sum(obi_vals) / len(obi_vals)) * 3.0)) if obi_vals else None
    )

    # orderflow_delta: (bull bars - bear bars) / n — directional bar count balance
    n = len(recent10)
    bulls = sum(1 for c in recent10 if c["close"] > c["open"])
    bears = sum(1 for c in recent10 if c["close"] < c["open"])
    ofd = max(-3.0, min(3.0, ((bulls - bears) / n) * 3.0)) if n > 0 else None

    # liquidity_pressure: avg (close-low)/(high-low) - 0.5 → where buyers/sellers end up per bar
    lp_vals = []
    for c in recent5:
        rng = c["high"] - c["low"]
        if rng > 0:
            lp_vals.append((c["close"] - c["low"]) / rng - 0.5)
    lp = max(-3.0, min(3.0, (sum(lp_vals) / len(lp_vals)) * 6.0)) if lp_vals else None

    return {
        "order_book_imbalance": obi,
        "orderflow_delta": ofd,
        "liquidity_pressure": lp,
    }


def _factor_score(
    indicators: Dict[str, Optional[float]], mapping: Dict[str, str]
) -> Optional[float]:
    """Simple mean of indicators whose keys appear in mapping values (legacy test API)."""
    vals = [indicators[k] for k in mapping.values() if indicators.get(k) is not None]
    if not vals:
        return None
    return sum(vals) / len(vals)


def _weighted_factor_score(
    indicators: Dict[str, Optional[float]],
    keys: List[str],
    corr_weights: Dict[str, float],
    use_abs: bool = False,
    factor_name: str = "",
    asset_type: str = "",
) -> Optional[float]:
    """Compute factor score as indicator-weighted, correlation-adjusted mean.

    Applies per-indicator weights from CONFIG["INDICATOR_WEIGHTS"][factor_name]
    on top of correlation adjustments. Missing indicator weight keys default to 1.0.
    use_abs=True for non-directional factors (always positive contribution).
    """
    # Per-indicator weights from config (empty dict = equal weighting)
    _iw_cfg = CONFIG.get("INDICATOR_WEIGHTS", {}).get(factor_name, {})
    if _iw_cfg and isinstance(next(iter(_iw_cfg.values()), None), dict):
        ind_weights = _iw_cfg.get(asset_type, _iw_cfg.get("crypto", {}))
    else:
        ind_weights = _iw_cfg

    vals = []
    wgts = []
    for k in keys:
        v = indicators.get(k)
        if v is not None:
            vals.append(abs(v) if use_abs else v)
            # Combined weight: indicator config weight × correlation adjustment
            iw = ind_weights.get(k, 1.0)
            cw = corr_weights.get(k, 1.0)
            wgts.append(iw * cw)
    if not vals:
        return None
    w_sum = sum(wgts)
    if w_sum <= 0:
        return None
    return sum(v * w / w_sum for v, w in zip(vals, wgts))


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
) -> Dict:
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
            log.warning(
                f"[FACTOR] {pair.get('display')} DATA QUALITY: EMA21={_ema21:.6f} vs close={_close:.4f} "
                f"— ratio {_ema21 / _close:.6f} suggests stale/corrupt data"
            )
    if _close is not None and _close > 0 and _atr_raw is not None and _atr_raw == 0:
        log.warning(
            f"[FACTOR] {pair.get('display')} DATA QUALITY: ATR=0 with close={_close:.6f} "
            f"— zero ATR indicates frozen/stale candle data"
        )

    # ── Gather indicators ────────────────────────────────────────────────
    indicators: Dict[str, Optional[float]] = {}

    # Trend direction — EMA crossover across all three timeframes (directional: +1/-1)
    # H1 entry timeframe
    ema21 = h1_snap.get("ema21")
    ema50 = h1_snap.get("ema50")
    if ema21 is not None and ema50 is not None and ema50 != 0:
        indicators["ema_trend"] = 1.0 if ema21 > ema50 else -1.0
    else:
        indicators["ema_trend"] = None

    # H4 momentum timeframe
    h4_ema21 = h4_snap.get("ema21")
    h4_ema50 = h4_snap.get("ema50")
    if h4_ema21 is not None and h4_ema50 is not None and h4_ema50 != 0:
        indicators["h4_ema_trend"] = 1.0 if h4_ema21 > h4_ema50 else -1.0
    else:
        indicators["h4_ema_trend"] = None

    # D1 tide timeframe (highest weight — primary directional bias)
    d1_ema21 = d1_snap.get("ema21")
    d1_ema50 = d1_snap.get("ema50")
    if d1_ema21 is not None and d1_ema50 is not None and d1_ema50 != 0:
        indicators["d1_ema_trend"] = 1.0 if d1_ema21 > d1_ema50 else -1.0
    else:
        indicators["d1_ema_trend"] = None

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
        # Accept volume if candles have real vol OR if ratio meaningfully deviates from 1.0
        # (deviation > 0.05 indicates an external source like Dukascopy supplied real data,
        #  vs the 1.0 fallback returned when no data is available)
        _has_candle_vol = any(
            c.get("vol", 0) > 0 for c in (h4_candles[-5:] if h4_candles else [])
        )
        _has_external_vol = asset_type == "forex" and volume_ratio != 1.0
        if _has_candle_vol or _has_external_vol:
            # Center around 1.0 (average), scale: 2x average → +3.0
            indicators["volume_ratio"] = max(-3.0, min(3.0, (volume_ratio - 1.0) * 3.0))
        else:
            indicators["volume_ratio"] = None  # No real volume data — exclude factor
    else:
        indicators["volume_ratio"] = None
    # OBV trend — only meaningful when real volume data exists (volume_ratio not None)
    # For forex/pairs without centralized volume, OBV computed on zero-vol candles is noise
    obv_raw = h4_snap.get("obv_trend")
    if indicators["volume_ratio"] is not None and obv_raw is not None:
        indicators["obv_trend"] = obv_raw
    else:
        indicators["obv_trend"] = None

    # Structure — fib proximity (non-directional)
    # fib_proximity returns +1/-1 when near a level, 0 when not near any level
    # 0 means "no structural info" — exclude from scoring (not "structure is bad")
    fib_prox = h4_snap.get("fib_proximity")
    indicators["fib_proximity"] = (
        fib_prox if fib_prox is not None and fib_prox != 0 else None
    )

    # Derivatives — funding rate (directional: negative funding = bullish for longs)
    if funding_rate is not None and funding_rate != 0:
        # Scale to z-score range: 0.01% funding → ±0.3, 0.1% → ±3.0
        indicators["funding_rate"] = max(-3.0, min(3.0, -funding_rate * 3000))
    else:
        indicators["funding_rate"] = None  # No funding data — exclude

    # COT positioning — CFTC net speculator z-score (directional, weekly)
    # Covers: all forex, BTC/ETH (CME), S&P/Nasdaq futures, gold/silver
    # NOTE: Now supports historical lookup during backtest using bar_time
    try:
        from cot_feed import get_cot_z as _get_cot_z

        _as_of = bar_time[:10] if bar_time else None
        _cot = _get_cot_z(pair.get("display", ""), as_of_date=_as_of)

        # Fade the herd for Forex and Commodities: Speculators are trapped at extremes
        if pair.get("type", "stock") in ("forex", "commodity") and _cot != 0.0:
            if abs(_cot) >= 2.0:
                # Extreme overcrowded positioning -> Reverse the signal (fade the herd)
                _cot = max(-3.0, min(3.0, -_cot * 1.5))
            elif abs(_cot) < 1.0:
                # Insignificant positioning -> ignore lagged data
                _cot = 0.0

        indicators["cot_z"] = float(_cot) if _cot != 0.0 else None
    except Exception:
        indicators["cot_z"] = None

    # Carry — interest rate differential z-score (directional, monthly)
    # Forex: base_rate - quote_rate; Indices/gold: inverted 10Y yield
    # NOTE: Now supports historical lookup during backtest using bar_time
    try:
        from carry_feed import get_carry_z as _get_carry_z

        _as_of = bar_time[:10] if bar_time else None
        _carry = _get_carry_z(pair.get("display", ""), as_of_date=_as_of)
        indicators["carry_z"] = float(_carry) if _carry != 0.0 else None
    except Exception:
        indicators["carry_z"] = None

    # Microstructure (directional if available)
    # Crypto: values injected from Binance/Bybit WS feeds via _micro_cache in athena.py
    # All others: computed from H4 OHLC price action (no order book required)
    _ws_obi = h4_snap.get("order_book_imbalance")
    _ws_lwl = h4_snap.get("liquidity_wall_detection")
    _ws_ofd = h4_snap.get("orderflow_delta")
    _ws_lp = h4_snap.get("liquidity_pressure")

    if _ws_obi is None and _ws_ofd is None and _ws_lp is None:
        # No WS data — compute candle-based proxies
        _cm = _candle_microstructure(h4_candles)
        indicators["order_book_imbalance"] = _cm["order_book_imbalance"]
        indicators["orderflow_delta"] = _cm["orderflow_delta"]
        indicators["liquidity_pressure"] = _cm["liquidity_pressure"]
        indicators["liquidity_wall_detection"] = None  # requires real order book
    else:
        indicators["order_book_imbalance"] = _ws_obi
        indicators["liquidity_wall_detection"] = _ws_lwl
        indicators["orderflow_delta"] = _ws_ofd
        indicators["liquidity_pressure"] = _ws_lp

    # ── Correlation filter (blueprint: abs(corr) > 0.8 → reduce weaker by 50%) ──
    corr_window = CONFIG.get("INDICATOR_CORRELATION_WINDOW", 200)
    corr_weights = _apply_correlation_filter(indicators, h4_candles, corr_window)

    # ── Factor mappings ──────────────────────────────────────────────────
    # Directional factors: sign matters (positive = bullish)
    directional_factors = {
        # Multi-timeframe EMA alignment: D1 tide + H4 momentum + H1 entry
        # All 3 aligned → ±1.0 (high conviction); mixed → near 0 (conflicting)
        "trend": ["ema_trend", "h4_ema_trend", "d1_ema_trend"],
        "momentum": ["rsi_z", "macdLine_z"],
        "derivatives": ["funding_rate", "cot_z"],
        "microstructure": [
            "order_book_imbalance",
            "liquidity_wall_detection",
            "orderflow_delta",
            "liquidity_pressure",
        ],
    }
    # Non-directional factors: abs value = quality/strength (always positive)
    nondirectional_factors = {
        "trend_strength": ["adx_z"],
        "volatility": ["atr_z", "bbWidth_z", "realized_vol_z"],
        "volume": ["volume_ratio", "obv_trend"],
        "structure": ["fib_proximity"],
        "carry": ["carry_z"],
    }

    # ── Compute factor scores (correlation-adjusted × indicator-weighted) ────
    factor_scores: Dict[str, Optional[float]] = {}
    for factor, keys in directional_factors.items():
        factor_scores[factor] = _weighted_factor_score(
            indicators,
            keys,
            corr_weights,
            use_abs=False,
            factor_name=factor,
            asset_type=asset_type,
        )
    for factor, keys in nondirectional_factors.items():
        factor_scores[factor] = _weighted_factor_score(
            indicators,
            keys,
            corr_weights,
            use_abs=True,
            factor_name=factor,
            asset_type=asset_type,
        )

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
    _raw_regime = detect_regime(h4_snap, asset_type, bb_width_pct=_bbw_pct).get(
        "regime", "UNKNOWN"
    )
    # Apply smoothing: require REGIME_SMOOTHING_BARS consecutive bars before accepting regime switch
    _pair_id = pair.get("display", pair.get("symbol", "unknown"))
    regime = _get_smoothed_regime(_pair_id, _raw_regime)
    regime_weights = CONFIG.get("REGIME_WEIGHTS", {}).get(regime.upper(), {})
    base_weights = CONFIG.get("FACTOR_WEIGHTS", {}).get(asset_type, {})

    # Map factor names to config weight keys
    _weight_key_map = {
        "trend": "trend",
        "momentum": "momentum",
        "derivatives": "derivatives",
        "microstructure": "microstructure",
        "trend_strength": "trend_strength",
        "volatility": "volatility",
        "volume": "volume",
        "structure": "structure",
        "carry": "carry",
    }
    weights: Dict[str, float] = {}
    all_factors = dict(directional_factors)
    all_factors.update(nondirectional_factors)
    for factor in all_factors:
        wk = _weight_key_map.get(factor, factor)
        base_w = base_weights.get(wk, 1.0)
        # If base weight is 0 (asset class disables this factor), regime cannot override
        weights[factor] = 0.0 if base_w == 0 else regime_weights.get(wk, base_w)

    weights = _apply_pair_profile_weight_rules(pair, weights)

    # Adaptive weight blending — adjust weights based on empirical factor performance
    # Only applies when learning_log has enough data (30+ trades for the asset class).
    # Blend rate is 30% (research-backed optimal range 0.25-0.35).
    # Disabled factors (weight=0) are never adjusted — adaptive cannot override explicit disable.
    try:
        from adaptive_weights import get_adaptive_weights
        import os

        _db = os.path.join(os.path.dirname(os.path.abspath(__file__)), "audit.db")
        _adaptive = get_adaptive_weights(_db, asset_type, regime)
        if _adaptive:
            import logging as _logging
            _logging.getLogger(__name__).info(
                f"[ADAPTIVE] applying learned weights for {asset_type}/{regime}"
            )
            for factor in weights:
                if weights[factor] > 0 and factor in _adaptive:
                    weights[factor] = _adaptive[factor]
    except Exception:
        pass  # Graceful degradation — use base weights if adaptive fails

    # ── Final aggregation ────────────────────────────────────────────────
    active_dir = {
        f: s
        for f, s in factor_scores.items()
        if s is not None and f in directional_factors
    }
    active_nondir = {
        f: s
        for f, s in factor_scores.items()
        if s is not None and f in nondirectional_factors
    }
    disabled_factors = [f for f, s in factor_scores.items() if s is None]

    # Minimum active factors guard: require at least 1 directional factor.
    # Prevents inflated scores driven solely by volatility (e.g. JSE stocks with no H4/H1 data).
    if not active_dir:
        log.debug(
            f"[FACTOR] {pair.get('display')} no active directional factors — score=0"
        )
        return {
            "final_score": 0.0,
            "direction": "LONG",
            "factor_scores": factor_scores,
            "weights": weights,
            "regime": regime,
            "filtered_indicators": indicators,
            "disabled_factors": disabled_factors,
            "directional_score": 0.0,
            "nondirectional_score": 0.0,
            "correlation_adjustments": {},
            "insufficient_factors": True,
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
        active_nondir = {
            f: s for f, s in active_nondir.items() if weights.get(f, 1.0) > 0
        }
    if active_nondir:
        nondir_w_sum = sum(weights.get(f, 1.0) for f in active_nondir)
        for f, s in active_nondir.items():
            nondir_score += (weights.get(f, 1.0) / nondir_w_sum) * s

    # Multiplicative combination: quality amplifies direction, cannot substitute for it.
    # nondir_score normalized to [0,1] (max theoretical is 3.0) then scales the dir contribution
    # from 0.6 (no quality) up to 1.0 (perfect quality), preserving 0–3.0 output range.
    # A minimum directional conviction gate prevents near-directionless setups from passing.
    _min_dir = CONFIG.get("FACTOR_MIN_DIRECTIONAL", 0.0)
    if abs(dir_score) < _min_dir:
        # Signal is directionless — score too low to qualify regardless of quality
        final_score = 0.0
    else:
        _nondir_norm = min(nondir_score / 3.0, 1.0)  # normalize quality to [0, 1]
        final_score = abs(dir_score) * (0.6 + _nondir_norm * 0.4)

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
        "insufficient_factors": False,
    }
