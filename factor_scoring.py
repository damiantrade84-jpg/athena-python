"""factor_scoring.py — Factor-based scoring engine with regime-aware weights and correlation filtering.

Computes factor scores from normalized indicators, applies regime weights, filters correlated indicators,
and aggregates to final signal score.

Blueprint compliance:
  - Regime detection (TRENDING/RANGING/HIGH_VOLATILITY/LOW_VOLATILITY) dynamically selects weights.
  - Indicator correlation filter: DISABLED (stub returns 1.0). Implementation in _pearson/_build_indicator_series ready for future re-enable.
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

_CRYPTO_COT_PAIRS = {"BTC/USDT", "ETH/USDT"}
_MICROSTRUCTURE_KEYS = (
    "order_book_imbalance",
    "liquidity_wall_detection",
    "orderflow_delta",
    "liquidity_pressure",
)


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


def _crypto_supports_cot(pair: dict) -> bool:
    return pair.get("display") in _CRYPTO_COT_PAIRS


def _optional_directional_keys(asset_type: str, pair: dict) -> tuple[str, ...]:
    if asset_type != "crypto":
        return ("funding_rate", "cot_z", "carry_z")
    keys = ["funding_rate"]
    if _crypto_supports_cot(pair):
        keys.append("cot_z")
    return tuple(keys)


def _has_live_microstructure(h4_snap: Dict) -> bool:
    age = h4_snap.get("microstructure_age_sec")
    try:
        if age is not None and float(age) > 45.0:
            return False
    except (TypeError, ValueError):
        pass
    return any(h4_snap.get(k) is not None for k in _MICROSTRUCTURE_KEYS)


# ── Regime smoothing state ────────────────────────────────────────────────────
# Regime smoothing is opt-in: callers only get multi-bar regime memory when they pass
# the same explicit regime_context object across successive analyses (for example, the
# shared full-scan context). Single ad-hoc analyze_pair() calls with regime_context=None
# intentionally use the raw regime for that one evaluation.
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
        # Keep only the last min_bars entries
        if len(hist) > min_bars:
            hist[:] = hist[-min_bars:]
        # Bootstrap: first observation commits immediately
        if committed is None:
            committed_map[pair_id] = raw_regime
            return raw_regime
        # Switch only when all of the last min_bars bars agree on the new regime
        if len(hist) >= min_bars and len(set(hist[-min_bars:])) == 1:
            committed_map[pair_id] = hist[-1]
        return committed_map[pair_id]


# ── Correlation filter ───────────────────────────────────────────────────────


# The following two functions are complete implementations used by _apply_correlation_filter()
# when the correlation filter is re-enabled. They are not called in the current codebase.
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
    """Correlation filter — CURRENTLY DISABLED (returns uniform weights of 1.0).

    The Pearson correlation implementation (_pearson, _build_indicator_series) is
    complete and ready to re-enable. To activate: replace the return statement with
    calls to those functions using the pattern from the original blueprint.

    Disabled because the multiplicative scoring formula already handles indicator
    redundancy through regime-weight dampening. Re-evaluate after collecting 6+
    months of live factor performance data via ai_learning.py.
    """
    return {k: 1.0 for k in indicator_scalars}


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
            "volume_momentum_spread": None,
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

    # volume_momentum_spread: momentum of volume-weighted directional conviction
    # Measures whether conviction is accelerating or fading (Swift Algo X concept)
    # Works for all asset classes with volume data. Returns None if vol absent.
    vms = None
    try:
        if len(h4_candles) >= 30:
            vms_bars = h4_candles[-30:]
            total_vol = sum(float(c.get("vol", 0) or 0) for c in vms_bars)
            if total_vol > 0:
                pressure = []
                for c in vms_bars:
                    rng = c["high"] - c["low"]
                    vol = float(c.get("vol", 0) or 0)
                    if rng > 0 and vol > 0:
                        signed = rng if c["close"] >= c["open"] else -rng
                        pressure.append((signed * vol) / total_vol)
                    else:
                        pressure.append(0.0)
                # Fast(5) minus slow(14) EMA of pressure = momentum of conviction
                def _ema_s(vals, p):
                    if len(vals) < p:
                        return []
                    k = 2.0 / (p + 1)
                    r = [sum(vals[:p]) / p]
                    for v in vals[p:]:
                        r.append(v * k + r[-1] * (1 - k))
                    return r
                fast = _ema_s(pressure, 5)
                slow = _ema_s(pressure, 14)
                if fast and slow:
                    spread = [f - s for f, s in zip(fast[-len(slow):], slow)]
                    if len(spread) >= 3:
                        mean_s = sum(spread) / len(spread)
                        std_s = (sum((x - mean_s)**2 for x in spread) / len(spread)) ** 0.5
                        if std_s > 1e-10:
                            vms = max(-3.0, min(3.0, (spread[-1] - mean_s) / std_s))
    except Exception:
        pass

    return {
        "order_book_imbalance": obi,
        "orderflow_delta": ofd,
        "liquidity_pressure": lp,
        "volume_momentum_spread": vms,
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
        ind_weights = _iw_cfg.get(asset_type, _iw_cfg.get("default", {}))
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


def _trend_indicator_weights(asset_type: str) -> Dict[str, float]:
    trend_cfg = (CONFIG.get("INDICATOR_WEIGHTS", {}) or {}).get("trend", {}) or {}
    configured = {}
    if isinstance(trend_cfg, dict):
        raw = trend_cfg.get(asset_type, trend_cfg.get("default", {}))
        if isinstance(raw, dict):
            configured = raw

    fallback = {
        "d1_ema_trend": 0.50,
        "h4_ema_trend": 0.30,
        "ema_trend": 0.20,
    }
    weights = {}
    for key, default_weight in fallback.items():
        try:
            weights[key] = float(configured.get(key, default_weight))
        except (TypeError, ValueError):
            weights[key] = default_weight
    total = sum(max(0.0, w) for w in weights.values())
    if total <= 0:
        return fallback
    return {k: max(0.0, v) / total for k, v in weights.items()}


def _coherent_trend_score(
    indicators: Dict[str, Optional[float]], asset_type: str
) -> tuple[Optional[float], dict]:
    """Build a coherence-aware multi-timeframe trend score.

    Uses weighted direction votes from D1/H4/H1 EMA alignment but avoids collapsing to
    near-zero when one timeframe is in pullback against the dominant trend.
    """
    tf_weights = _trend_indicator_weights(asset_type)
    votes = []
    for key, w in tf_weights.items():
        v = indicators.get(key)
        if v is not None and w > 0:
            votes.append((key, 1.0 if v > 0 else -1.0, w))

    if not votes:
        return None, {
            "agreement_count": 0,
            "total_count": 0,
            "coherence_ratio": 0.0,
            "dominant_direction": None,
            "weighted_balance": 0.0,
        }

    long_w = sum(w for _, d, w in votes if d > 0)
    short_w = sum(w for _, d, w in votes if d < 0)
    total_w = long_w + short_w
    if total_w <= 0:
        return None, {
            "agreement_count": 0,
            "total_count": len(votes),
            "coherence_ratio": 0.0,
            "dominant_direction": None,
            "weighted_balance": 0.0,
        }

    if long_w == short_w:
        # Tie-break by D1 if available, otherwise default LONG for consistency.
        d1 = indicators.get("d1_ema_trend")
        dominant_sign = 1.0 if d1 is None or d1 > 0 else -1.0
    else:
        dominant_sign = 1.0 if long_w > short_w else -1.0

    dominant_w = long_w if dominant_sign > 0 else short_w
    coherence_ratio = max(0.5, min(1.0, dominant_w / total_w))
    agreement_count = sum(1 for _, d, _ in votes if d == dominant_sign)
    # Floor keeps trend signal alive on 2-of-3 alignment while still penalizing mixed TFs.
    # Scaled by 3.0 to align mathematically with z-score bounded indicators (±3.0)
    magnitude = (0.35 + (0.65 * coherence_ratio)) * 3.0
    trend_score = dominant_sign * magnitude
    weighted_balance = (long_w - short_w) / total_w

    return trend_score, {
        "agreement_count": agreement_count,
        "total_count": len(votes),
        "coherence_ratio": round(coherence_ratio, 4),
        "dominant_direction": "LONG" if dominant_sign > 0 else "SHORT",
        "weighted_balance": round(weighted_balance, 4),
    }


def _directional_confidence_multiplier(abs_dir: float, min_dir: float, soft_span: float) -> float:
    """Smooth confidence curve replacing hard directional cliff.

    abs_dir: absolute directional score (0..~1)
    min_dir: directional threshold center point
    soft_span: smooth transition width
    """
    if soft_span <= 0:
        return 1.0 if abs_dir >= min_dir else 0.0
    # Logistic centered at min_dir. 0.5 at threshold, smooth tails.
    x = (abs_dir - min_dir) / soft_span
    k = 4.0
    return 1.0 / (1.0 + math.exp(-k * x))


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
) -> Dict:
    """Compute factor scores, apply regime weights, and aggregate to final score.

    Returns dict with final_score (always >= 0), direction, factor_scores, weights, regime, etc.
    """
    asset_type = pair.get("type", "stock")
    is_crypto = asset_type == "crypto"

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
        # Scale to z-score range. 
        # Standard neutral Binance funding is 0.01% (0.0001). Offset so neutral = 0.0.
        # Scale remaining deviation: 0.03% deviation translates to ±3.0.
        adjusted_funding = funding_rate - 0.0001
        # Recalibrated: 0.04% above neutral → score of 2.0 (was maxing at 0.02%)
        indicators["funding_rate"] = max(-3.0, min(3.0, -adjusted_funding * 5000))
    else:
        indicators["funding_rate"] = None  # No funding data — exclude

    # COT positioning — CFTC net speculator z-score (directional, weekly)
    # Covers: all forex, BTC/ETH (CME), S&P/Nasdaq futures, gold/silver
    # NOTE: Now supports historical lookup during backtest using bar_time
    if is_crypto and not _crypto_supports_cot(pair):
        indicators["cot_z"] = None
    else:
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
    if is_crypto:
        indicators["carry_z"] = None
    else:
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

    if is_crypto:
        if _has_live_microstructure(h4_snap):
            indicators["order_book_imbalance"] = _ws_obi
            indicators["liquidity_wall_detection"] = _ws_lwl
            indicators["orderflow_delta"] = _ws_ofd
            indicators["liquidity_pressure"] = _ws_lp
        else:
            indicators["order_book_imbalance"] = None
            indicators["liquidity_wall_detection"] = None
            indicators["orderflow_delta"] = None
            indicators["liquidity_pressure"] = None
        # VMS is candle-derived directional conviction. For crypto we route it into
        # momentum instead of the disabled microstructure bucket so it has controlled impact.
        _cm_vms = _candle_microstructure(h4_candles)
        indicators["volume_momentum_spread"] = _cm_vms["volume_momentum_spread"]
    elif _ws_obi is None and _ws_ofd is None and _ws_lp is None:
        # No WS data — compute candle-based proxies (includes VMS)
        _cm = _candle_microstructure(h4_candles)
        indicators["order_book_imbalance"] = _cm["order_book_imbalance"]
        indicators["orderflow_delta"] = _cm["orderflow_delta"]
        indicators["liquidity_pressure"] = _cm["liquidity_pressure"]
        indicators["liquidity_wall_detection"] = None  # requires real order book
        indicators["volume_momentum_spread"] = _cm["volume_momentum_spread"]
    else:
        indicators["order_book_imbalance"] = _ws_obi
        indicators["liquidity_wall_detection"] = _ws_lwl
        indicators["orderflow_delta"] = _ws_ofd
        indicators["liquidity_pressure"] = _ws_lp
        # VMS is always candle-based even when WS data is available for other keys
        _cm_vms = _candle_microstructure(h4_candles)
        indicators["volume_momentum_spread"] = _cm_vms["volume_momentum_spread"]

    # ── Correlation filter (blueprint: abs(corr) > 0.8 → reduce weaker by 50%) ──
    corr_window = CONFIG.get("INDICATOR_CORRELATION_WINDOW", 200)
    corr_weights = _apply_correlation_filter(indicators, h4_candles, corr_window)

    # ── Factor mappings ──────────────────────────────────────────────────
    # Directional factors: sign matters (positive = bullish)
    derivative_keys = ["funding_rate"]
    if not is_crypto or _crypto_supports_cot(pair):
        derivative_keys.append("cot_z")

    momentum_keys = ["rsi_z", "macdLine_z"]
    microstructure_keys = [
        "order_book_imbalance",
        "liquidity_wall_detection",
        "orderflow_delta",
        "liquidity_pressure",
    ]
    if is_crypto:
        momentum_keys.append("volume_momentum_spread")
    else:
        microstructure_keys.append("volume_momentum_spread")

    directional_factors = {
        # Multi-timeframe EMA alignment: D1 tide + H4 momentum + H1 entry
        # All 3 aligned → ±1.0 (high conviction); mixed → near 0 (conflicting)
        "trend": ["ema_trend", "h4_ema_trend", "d1_ema_trend"],
        "momentum": momentum_keys,
        "derivatives": derivative_keys,
        "microstructure": microstructure_keys,
    }
    if not is_crypto:
        directional_factors["carry"] = ["carry_z"]
    # Non-directional factors: abs value = quality/strength (always positive)
    nondirectional_factors = {
        "trend_strength": ["adx_z"],
        "volatility": ["atr_z", "bbWidth_z", "realized_vol_z"],
        "volume": ["volume_ratio", "obv_trend"],
        "structure": ["fib_proximity"],
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
    trend_coherence = {
        "agreement_count": 0,
        "total_count": 0,
        "coherence_ratio": 0.0,
        "dominant_direction": None,
        "weighted_balance": 0.0,
    }
    _trend_score, trend_coherence = _coherent_trend_score(indicators, asset_type)
    if _trend_score is not None:
        factor_scores["trend"] = _trend_score

    # ── Determine direction from directional factors ─────────────────────
    # ── Regime detection ─────────────────────────────────────────────────
    _bbw_pct = h4_snap.get("bbWidth_pct") or h4_snap.get("bb_width_pct")
    _raw_regime = detect_regime(h4_snap, asset_type, bb_width_pct=_bbw_pct).get(
        "regime", "UNKNOWN"
    )
    # Apply smoothing: require REGIME_SMOOTHING_BARS consecutive bars before accepting regime switch
    _pair_id = pair.get("display", pair.get("symbol", "unknown"))
    regime = _get_smoothed_regime(regime_context, _pair_id, _raw_regime)
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
    if is_crypto:
        for factor, cap in (CONFIG.get("CRYPTO_FACTOR_WEIGHT_CAPS", {}) or {}).items():
            if factor not in weights:
                continue
            try:
                weights[factor] = min(float(weights[factor]), float(cap))
            except (TypeError, ValueError):
                continue

    # Adaptive weight blending — adjust weights based on empirical factor performance
    # Only applies when learning_log has enough data (30+ trades for the asset class).
    # Blend rate is 30% (research-backed optimal range 0.25-0.35).
    # Disabled factors (weight=0) are never adjusted — adaptive cannot override explicit disable.
    if CONFIG.get("ADAPTIVE_WEIGHTS_ENABLED", False):
        try:
            from adaptive_weights import get_adaptive_weights
            import os

            _db = os.path.join(os.path.dirname(os.path.abspath(__file__)), "audit.db")
            _adaptive = get_adaptive_weights(_db, asset_type, regime)
            if _adaptive:
                import logging as _logging
                _logging.getLogger(__name__).debug(
                    f"[ADAPTIVE] applying learned weights for {asset_type}/{regime}"
                )
                for factor in weights:
                    if weights[factor] > 0 and factor in _adaptive:
                        try:
                            weights[factor] = max(
                                0.0, float(weights[factor]) * float(_adaptive[factor])
                            )
                        except (TypeError, ValueError):
                            continue
        except Exception:
            pass  # Graceful degradation — use base weights if adaptive fails

    # ── Final aggregation ────────────────────────────────────────────────
    if is_crypto:
        for factor, cap in (CONFIG.get("CRYPTO_FACTOR_WEIGHT_CAPS", {}) or {}).items():
            if factor not in weights:
                continue
            try:
                weights[factor] = min(float(weights[factor]), float(cap))
            except (TypeError, ValueError):
                continue

    active_dir = {
        f: s
        for f, s in factor_scores.items()
        if s is not None and f in directional_factors and weights.get(f, 1.0) > 0
    }
    active_nondir = {
        f: s
        for f, s in factor_scores.items()
        if s is not None and f in nondirectional_factors and weights.get(f, 1.0) > 0
    }
    disabled_factors = [f for f, s in factor_scores.items() if s is None]

    _base_min_dir = float(
        CONFIG.get(
            f"FACTOR_MIN_DIRECTIONAL_{asset_type.upper()}",
            CONFIG.get("FACTOR_MIN_DIRECTIONAL", 0.0),
        )
    )
    _base_soft_span = float(
        CONFIG.get(
            f"FACTOR_DIRECTIONAL_SOFT_SPAN_{asset_type.upper()}",
            CONFIG.get("FACTOR_DIRECTIONAL_SOFT_SPAN", 0.20),
        )
    )
    dir_sum = sum(active_dir.values()) if active_dir else 0.0
    direction = "LONG" if dir_sum > 0 else "SHORT"

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
            "min_directional_failed": True,
            "active_directional_factors": [],
            "active_nondirectional_factors": list(active_nondir.keys()),
            "min_directional_threshold": _base_min_dir,
            "directional_confidence_multiplier": 0.0,
            "effective_min_directional": _base_min_dir,
            "trend_coherence": trend_coherence,
            "missing_directional_optional_count": max(
                0, len(_optional_directional_keys(asset_type, pair))
            ),
            "optional_factor_coverage": 0.0
            if _optional_directional_keys(asset_type, pair)
            else 1.0,
        }

    dir_score = 0.0
    if active_dir:
        dir_w_sum = sum(weights.get(f, 1.0) for f in active_dir)
        for f, s in active_dir.items():
            dir_score += (weights.get(f, 1.0) / dir_w_sum) * s

    nondir_score = 0.0
    if active_nondir:
        nondir_w_sum = sum(weights.get(f, 1.0) for f in active_nondir)
        for f, s in active_nondir.items():
            nondir_score += (weights.get(f, 1.0) / nondir_w_sum) * s

    # Optional directional context coverage.
    optional_directional_keys = _optional_directional_keys(asset_type, pair)
    optional_present = sum(
        1 for k in optional_directional_keys if indicators.get(k) is not None
    )
    optional_coverage = (
        optional_present / float(len(optional_directional_keys))
        if optional_directional_keys
        else 1.0
    )
    missing_optional = max(0, len(optional_directional_keys) - optional_present)

    # Multiplicative combination with smooth directional confidence (no hard cliff).
    # As optional directional context gets sparse, use a slightly softer effective threshold
    # so missing feeds do not collapse otherwise-valid directional setups.
    _min_dir = _base_min_dir
    _soft_span = _base_soft_span
    _effective_min_dir = float(_min_dir) * (0.75 + (0.25 * optional_coverage))
    _dir_conf = _directional_confidence_multiplier(abs(dir_score), _effective_min_dir, _soft_span)
    _nondir_norm = min(nondir_score / 3.0, 1.0)  # normalize quality to [0, 1]
    _quality_mult = 0.6 + (_nondir_norm * 0.4)
    final_score = abs(dir_score) * _quality_mult * _dir_conf

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
        "min_directional_failed": abs(dir_score) < float(_min_dir),
        "active_directional_factors": list(active_dir.keys()),
        "active_nondirectional_factors": list(active_nondir.keys()),
        "min_directional_threshold": float(_min_dir),
        "directional_confidence_multiplier": _dir_conf,
        "effective_min_directional": _effective_min_dir,
        "trend_coherence": trend_coherence,
        "missing_directional_optional_count": missing_optional,
        "optional_factor_coverage": round(optional_coverage, 4),
    }
