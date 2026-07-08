"""Engine A — continuous quant quality scorer (ground-up rebuild).

Replaces V3 setup-detection scoring with a continuous, no-veto quality model.
Every pair always receives a direction + a 0..3.0 confluence score built from
weighted, normalized indicator components. Weak or conflicting components lower
the score; nothing silently filters a pair out — NO_SIGNAL is reserved for
invalid candle data and is handled by the evaluator, not here.

Division of labour (intentional reuse, not rebuild):
  * indicators.calc_indicators_with_normalized(..., score_group=group) supplies
    per-score_group indicator periods (RSI 18 forex / 12 crypto, EMA trend 26/18,
    etc.) so chart <-> Engine A parity is preserved.
  * engine_a_v3.levels builds SL/TP geometry.
This module owns ONLY component scoring + aggregation.

Components (each -> signed direction in [-1,1] and quality in [0,1]):
  trend (multi-TF EMA stack), momentum (RSI/MACD/DI+ADX), location (pullback /
  mean-reversion timing), volume/flow. These four are the only components that
  feed direction/confluence — engine_a_v3.profile's CORE_COMPONENTS schema
  enforces the price-core weight table. Optional orthogonal subsystem factors
  (intermarket/carry/sentiment/macro/microstructure) wire in when
  ``ENGINE_A_V3_SUBSYSTEMS.ENABLED`` is true; default off preserves the
  four-factor path. Research ablation: shadow_scorer.py.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from factor_scoring import _adx_multiplier_from_value, _resolve_adx_thresholds
from engine_a_scoring_profile import resolve_engine_a_scoring_profile
from engine_a_v3.profile import CORE_COMPONENTS
from engine_a_v3.subsystems import (
    ST_AVAILABLE,
    ST_NA,
    ST_NEUTRAL,
    ST_UNAVAILABLE,
    SUBSYSTEM_FACTORS,
    resolve_subsystem_weights,
    subsystems_enabled,
    subsystem_component,
)


# ── scale ──────────────────────────────────────────────────────────────────────
MAX_SCORE = 3.0

# Multi-timeframe trend weights by horizon (Elder triple screen: D1 tide,
# H4 momentum, H1 entry). Intraday emphasises faster TFs; swing emphasises D1.
_TF_WEIGHTS: dict[str, dict[str, float]] = {
    "swing":    {"D1": 0.50, "H4": 0.32, "H1": 0.18},
    "intraday": {"D1": 0.28, "H4": 0.40, "H1": 0.32},
}


def _resolve_v3_tf_weights(score_group: str, asset_type: str, horizon: str) -> dict[str, float]:
    """Resolve per-group TF weights for the V3 trend component.

    Wires V3 to ``ENGINE_A_SCORING_PROFILE`` so per-group TF overrides take live
    effect (e.g., energy_oil/commodity_other drop D1 and weight H4+H1 0.55/0.45;
    us_stock_single uses 0.40/0.35/0.25). The profile's ``trend_layers`` map each
    ``weight_key`` to a ``tf``; the returned dict is keyed by TF (D1/H4/H1) so
    ``_trend_component`` can consume it directly. Falls back to the hardcoded
    ``_TF_WEIGHTS[horizon]`` when the profile is disabled or yields no usable
    weights, preserving prior behaviour for unaudited groups / disabled config.
    """
    try:
        profile = resolve_engine_a_scoring_profile(
            score_group=score_group, asset_type=asset_type, style=horizon
        )
    except Exception:
        return dict(_TF_WEIGHTS[horizon])
    if not profile.get("enabled"):
        return dict(_TF_WEIGHTS[horizon])
    layers = profile.get("trend_layers") or []
    weights = profile.get("trend_weights") or {}
    tf_weights: dict[str, float] = {}
    for layer in layers:
        tf = str(layer.get("tf", "")).upper()
        wkey = str(layer.get("weight_key", "")).strip()
        if not tf or not wkey or wkey not in weights:
            continue
        try:
            tf_weights[tf] = float(weights[wkey])
        except (TypeError, ValueError):
            pass
    return tf_weights or dict(_TF_WEIGHTS[horizon])


def _resolve_v3_momentum_tf(score_group: str, asset_type: str, horizon: str) -> str:
    """Resolve the per-group momentum anchor TF for V3.

    Wires V3 to ``ENGINE_A_SCORING_PROFILE.momentum_tf`` so per-group overrides
    take live effect (e.g., bond_tlt anchors momentum on D1 instead of the
    universal H4 — D1 RSI/MACD are smoother for a slow-swing instrument). Falls
    back to "H4" when the profile is disabled or momentum_tf is absent.
    """
    try:
        profile = resolve_engine_a_scoring_profile(
            score_group=score_group, asset_type=asset_type, style=horizon
        )
    except Exception:
        return "H4"
    if not profile.get("enabled"):
        return "H4"
    return str(profile.get("momentum_tf") or "H4").upper() or "H4"


def _resolve_v3_entry_tf(score_group: str, asset_type: str, horizon: str) -> str:
    """Resolve primary entry TF for V3 scoring.

    Default remains H1 (intraday) / H4 (swing). Only ``BY_SCORE_GROUP.execution_tf``
    overrides change the entry anchor — not the universal ``BY_STYLE.execution_tf``,
    which describes chart/execution context rather than the quant entry bar.
    """
    fallback = "H1" if str(horizon).lower() == "intraday" else "H4"
    try:
        from config import CONFIG

        by_group = (CONFIG.get("ENGINE_A_SCORING_PROFILE") or {}).get("BY_SCORE_GROUP") or {}
        group_cfg = by_group.get(score_group) or {}
        override = str(group_cfg.get("execution_tf") or "").strip().upper()
        if override:
            return override
    except Exception:
        pass
    return fallback


# route.family -> asset_type expected by calc_indicators_with_normalized.
_FAMILY_ASSET = {
    "forex": "forex",
    "crypto": "crypto",
    "commodity": "commodity",
    "index": "index",
    "equity_etf": "stock",
}

# ── small numeric helpers ────────────────────────────────────────────────────
def _f(value: Any, default: float | None = None) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    if out != out:  # NaN
        return default
    return out


def _clamp(value: float, lo: float, hi: float) -> float:
    return lo if value < lo else hi if value > hi else value


def _clamp01(value: float) -> float:
    return _clamp(value, 0.0, 1.0)


# ── component results ────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Component:
    signal: float   # -1..1 directional (sign = LONG/SHORT)
    quality: float  # 0..1 confidence/cleanliness
    available: bool = True


@dataclass
class QuantScore:
    direction: str           # LONG | SHORT | FLAT
    confluence_score: float  # 0..MAX_SCORE
    max_score: float
    score_norm: float        # 0..1
    conviction: float        # 0..1
    decision: str            # TRADE | WATCH
    threshold: float
    level_style: str         # trend | mean_reversion
    factor_scores: dict[str, Any]
    factor_diagnostics: dict[str, Any]
    components: dict[str, Component] = field(default_factory=dict)


# ── trend (multi-timeframe EMA stack) ────────────────────────────────────────
def _tf_trend(snap: Mapping[str, Any]) -> tuple[float, str, bool]:
    """One timeframe's EMA-stack vote -> (-1..1, label, available)."""
    close = _f(snap.get("close"))
    e_trend = _f(snap.get("ema21"))   # trend EMA (group period)
    e_mom = _f(snap.get("ema50"))     # momentum EMA
    e_long = _f(snap.get("ema200"))   # long EMA
    if close is None or e_trend is None or e_mom is None or e_long is None:
        return 0.0, "FLAT", False
    votes = [
        1.0 if close > e_trend else -1.0,
        1.0 if e_trend > e_mom else -1.0,
        1.0 if e_mom > e_long else -1.0,
    ]
    slope = _f(snap.get("ema200Slope10"), 0.0) or 0.0
    if slope:
        votes.append(1.0 if slope > 0 else -1.0)
    score = sum(votes) / len(votes)
    label = "UP" if score > 0.34 else "DOWN" if score < -0.34 else "FLAT"
    return score, label, True


def _trend_component(
    snaps: dict[str, Mapping[str, Any]],
    tf_w: Mapping[str, float],
    *,
    entry_candles: list[dict] | None = None,
    indicator_periods: Mapping[str, int] | None = None,
    entry_tf: str = "H1",
) -> tuple[Component, dict[str, str]]:
    parts: dict[str, float] = {}
    coherence: dict[str, str] = {}
    weighted = 0.0
    total_w = 0.0
    for tf, w in tf_w.items():
        snap = snaps.get(tf)
        if not snap:
            continue
        score, label, available = _tf_trend(snap)
        if not available:
            continue
        parts[tf] = score
        coherence[tf.lower()] = label
        weighted += w * score
        total_w += w
    if total_w <= 0:
        return Component(0.0, 0.0), coherence
    weighted /= total_w
    vals = list(parts.values())
    if len(vals) >= 2:
        mean = sum(vals) / len(vals)
        dispersion = sum(abs(v - mean) for v in vals) / len(vals)
        coherence_q = max(0.0, 1.0 - dispersion)
    else:
        coherence_q = abs(weighted)
    quality = abs(weighted) * (0.5 + 0.5 * coherence_q)
    quality *= _trend_health_mult(
        weighted, snaps, entry_candles, indicator_periods, entry_tf=entry_tf
    )
    return Component(_clamp(weighted, -1.0, 1.0), _clamp01(quality)), coherence


def _trend_alignment_age(
    candles: list[dict], *, ema_period: int = 21, max_lookback: int = 25
) -> int:
    """Count consecutive entry-TF bars with the same close vs EMA side as the latest bar."""
    if len(candles) < ema_period + 2:
        return 0
    from engine_a_v3.setups import _ema

    closes = [float(c["close"]) for c in candles if _f(c.get("close")) is not None]
    if len(closes) < ema_period + 2:
        return 0
    ema = _ema(closes, ema_period)
    latest_side = closes[-1] > ema[-1]
    age = 0
    start = len(closes) - 2
    stop = max(ema_period - 1, start - max_lookback)
    for idx in range(start, stop, -1):
        if (closes[idx] > ema[idx]) == latest_side:
            age += 1
        else:
            break
    return age


def _trend_health_mult(
    trend_signal: float,
    snaps: dict[str, Mapping[str, Any]],
    entry_candles: list[dict] | None,
    indicator_periods: Mapping[str, int] | None,
    *,
    entry_tf: str = "H1",
) -> float:
    """Penalize stale or weakening trends (config-gated, default on)."""
    enabled = True
    start_bars = 8
    bar_penalty = 0.03
    floor = 0.75
    adx_weakening_penalty = 0.15
    try:
        from config import CONFIG

        cfg = CONFIG.get("ENGINE_A_V3_TREND_HEALTH") or {}
        enabled = bool(cfg.get("ENABLED", True))
        start_bars = int(cfg.get("STALE_ALIGNMENT_BARS", 8))
        bar_penalty = float(cfg.get("BAR_PENALTY", 0.03))
        floor = float(cfg.get("FLOOR", 0.75))
        adx_weakening_penalty = float(cfg.get("ADX_WEAKENING_PENALTY", 0.15))
    except Exception:
        pass
    if not enabled or abs(trend_signal) < 0.34:
        return 1.0

    mult = 1.0
    _entry_key = str(entry_tf or "H1").upper()
    entry_snap = (
        snaps.get(_entry_key)
        or snaps.get("H4")
        or snaps.get("H1")
        or snaps.get("D1")
        or {}
    )
    adx_slope = _f(entry_snap.get("adxSlope"), 0.0) or 0.0
    if trend_signal > 0 and adx_slope < 0:
        mult *= _clamp(1.0 + adx_weakening_penalty * adx_slope, floor, 1.0)
    elif trend_signal < 0 and adx_slope > 0:
        mult *= _clamp(1.0 - adx_weakening_penalty * adx_slope, floor, 1.0)

    adx = _f(entry_snap.get("adx"), 0.0) or 0.0
    adx_prev = _f(entry_snap.get("adxPrev"))
    if adx_prev is not None and adx > 25 and abs(adx - adx_prev) < 1.0:
        mult *= 0.85

    if entry_candles:
        ema_period = 21
        if indicator_periods and "ema_trend" in indicator_periods:
            ema_period = int(indicator_periods["ema_trend"])
        age = _trend_alignment_age(entry_candles, ema_period=ema_period)
        if age > start_bars:
            mult *= max(floor, 1.0 - bar_penalty * (age - start_bars))
    return _clamp(mult, floor, 1.0)


# ── momentum (RSI / MACD / DI+ADX) ───────────────────────────────────────────
def _momentum_blend_enabled() -> bool:
    try:
        from config import CONFIG

        cfg = CONFIG.get("ENGINE_A_V3_MOMENTUM_BLEND") or {}
        return bool(cfg.get("ENABLED", True))
    except Exception:
        return True


def _momentum_component(
    snap: Mapping[str, Any], asset_type: str, score_group: str
) -> tuple[Component, dict[str, Any]]:
    diag: dict[str, Any] = {
        "adxValue": None,
        "adxMultiplier": None,
        "diAlignMult": None,
        "rsiTerm": None,
        "diTerm": None,
        "macdSlopeTerm": None,
    }
    if not snap:
        return Component(0.0, 0.0), diag

    rsi_w, di_w, macd_w = 0.35, 0.35, 0.30
    if _momentum_blend_enabled():
        try:
            from config import CONFIG

            cfg = CONFIG.get("ENGINE_A_V3_MOMENTUM_BLEND") or {}
            rsi_w = float(cfg.get("RSI_WEIGHT", 0.35))
            di_w = float(cfg.get("DI_WEIGHT", 0.35))
            macd_w = float(cfg.get("MACD_SLOPE_WEIGHT", 0.30))
        except Exception:
            pass
    else:
        rsi_w = di_w = macd_w = 1.0 / 3.0

    weighted_signal = 0.0
    weight_total = 0.0
    quality_terms: list[float] = []

    rsi = _f(snap.get("rsi"))
    if rsi is not None:
        rsi_term = _clamp((rsi - 50.0) / 30.0, -1.0, 1.0)
        rsi_quality = _clamp01(abs(rsi - 50.0) / 30.0)
        weighted_signal += rsi_w * rsi_term
        weight_total += rsi_w
        quality_terms.append(rsi_quality)
        diag["rsiTerm"] = round(rsi_term, 4)

    di_p = _f(snap.get("plusDI"))
    di_m = _f(snap.get("minusDI"))
    if di_p is not None and di_m is not None and (di_p + di_m) > 0:
        di_term = _clamp((di_p - di_m) / (di_p + di_m), -1.0, 1.0)
        weighted_signal += di_w * di_term
        weight_total += di_w
        quality_terms.append(abs(di_term))
        diag["diAlignMult"] = round(1.0 + 0.3 * di_term, 4)
        diag["diTerm"] = round(di_term, 4)

    hist = _f(snap.get("macdHist"))
    hist_prev = _f(snap.get("macdHistPrev"))
    if hist is not None:
        if hist_prev is not None and _momentum_blend_enabled():
            slope = hist - hist_prev
            macd_term = _clamp(slope / max(abs(hist_prev), 0.05), -1.0, 1.0)
            if abs(macd_term) < 0.05 and hist != 0.0:
                macd_term = 0.8 if hist > 0 else -0.8 if hist < 0 else 0.0
        else:
            macd_term = 1.0 if hist > 0 else -1.0 if hist < 0 else 0.0
            if hist_prev is not None and macd_term != 0.0:
                rising = hist > hist_prev
                strengthening = (macd_term > 0 and rising) or (macd_term < 0 and not rising)
                macd_term *= 1.0 if strengthening else 0.6
            elif macd_term != 0.0:
                macd_term *= 0.8
        weighted_signal += macd_w * macd_term
        weight_total += macd_w
        quality_terms.append(_clamp01(abs(macd_term)))
        diag["macdSlopeTerm"] = round(macd_term, 4)

    signal = weighted_signal / weight_total if weight_total > 0 else 0.0

    adx_raw = snap.get("adx")
    adx_missing = adx_raw is None
    if not adx_missing:
        try:
            adx_probe = float(adx_raw)
            adx_missing = adx_probe != adx_probe  # NaN
        except (TypeError, ValueError):
            adx_missing = True
    if adx_missing:
        diag["adxMissing"] = True
        try:
            from config import CONFIG

            if CONFIG.get("ADX_MISSING_BOTH_ABORT", False):
                diag["adxHardAbort"] = True
                diag["adxAbortReason"] = "missing_both_abort"
                return Component(0.0, 0.0, available=False), diag
        except Exception:
            pass

    adx = _f(adx_raw, 0.0) or 0.0
    diag["adxValue"] = round(adx, 2)
    trend_min, hard_fail = _resolve_adx_thresholds(asset_type, score_group)
    adx_mult = _adx_multiplier_from_value(adx, trend_min, hard_fail)
    diag["adxMultiplier"] = round(adx_mult, 4)
    if adx_mult <= 0.0:
        diag["adxHardFail"] = True
    base_quality = sum(quality_terms) / len(quality_terms) if quality_terms else 0.0
    quality = _clamp01(base_quality * adx_mult)
    return Component(_clamp(signal, -1.0, 1.0), quality), diag


def _resolve_mr_adx_ceiling(asset_type: str, score_group: str) -> float:
    """Per-group ADX ceiling for the location mean-reversion regime switch.

    Defaults to ``ADX_TREND_MIN_CLASS`` via the canonical factor_scoring resolver
    (same source as momentum quality). Optional override:
    ``ENGINE_A_V3_MEAN_REVERSION.ADX_MAX``.
    """
    trend_min, _hard_fail = _resolve_adx_thresholds(asset_type, score_group)
    ceiling = trend_min
    try:
        from config import CONFIG

        mr_cfg = CONFIG.get("ENGINE_A_V3_MEAN_REVERSION") or {}
        override = mr_cfg.get("ADX_MAX")
        if override is not None:
            ceiling = float(override)
    except Exception:
        pass
    return ceiling


# ── location (pullback timing / mean-reversion regime) ───────────────────────
def _location_component(
    snap: Mapping[str, Any], asset_type: str, score_group: str
) -> tuple[Component, str]:
    """Entry timing. In a trend, a small pullback toward the trend EMA is the
    best entry (high quality); over-extension lowers quality but never vetoes.
    In a weak-ADX, BB-stretched regime, flips to a mean-reversion fade signal."""
    if not snap:
        return Component(0.0, 0.5), "trend"
    close = _f(snap.get("close"))
    e_trend = _f(snap.get("ema21"))
    atr = _f(snap.get("atr"))
    if close is None or e_trend is None or atr is None or atr <= 0:
        return Component(0.0, 0.5), "trend"
    dist = (close - e_trend) / atr  # >0 above trend EMA
    adx = _f(snap.get("adx"), 0.0) or 0.0
    bb_u = _f(snap.get("bbUpper"))
    bb_l = _f(snap.get("bbLower"))
    stretched = bool((bb_u is not None and close > bb_u) or (bb_l is not None and close < bb_l))
    mr_adx_ceiling = _resolve_mr_adx_ceiling(asset_type, score_group)

    if adx < mr_adx_ceiling and stretched:
        # Range fade: signal opposite to the stretch; quality scales with stretch.
        signal = -1.0 if (bb_u is not None and close > bb_u) else 1.0
        quality = _clamp01(abs(dist) / 3.0)
        return Component(signal, quality), "mean_reversion"

    # Trend timing: quality peaks near the EMA, decays with extension.
    extension = abs(dist)
    quality = _clamp01(1.0 - max(0.0, extension - 0.5) / 3.0)
    signal = _clamp(dist / 2.0, -0.5, 0.5)  # mild directional bias only
    return Component(signal, quality), "trend"


# ── volatility regime (per-component quality multipliers) ────────────────────
def _volatility_gating_enabled() -> bool:
    try:
        from config import CONFIG

        cfg = CONFIG.get("ENGINE_A_V3_VOLATILITY_GATING") or {}
        return bool(cfg.get("ENABLED", True))
    except Exception:
        return True


def _component_vol_mults(snap: Mapping[str, Any]) -> dict[str, float]:
    """Per-component volatility/regime multipliers (default global 1.0)."""
    if not snap or not _volatility_gating_enabled():
        return {name: 1.0 for name in CORE_COMPONENTS}
    adx_pct = _f(snap.get("adx_pct"))
    atr_pct = _f(snap.get("atr_pct"))
    trend_mult = 1.0
    momentum_mult = 1.0
    location_mult = 1.0
    volume_mult = 1.0
    if adx_pct is not None:
        trend_mult += 0.08 * (adx_pct - 0.5)
        momentum_mult += 0.12 * (adx_pct - 0.5)
    if atr_pct is not None:
        location_mult += 0.06 * (0.5 - atr_pct)
        if atr_pct > 0.9:
            volume_mult -= 0.08
        if atr_pct > 0.9:
            trend_mult -= 0.06
    return {
        "trend": _clamp(trend_mult, 0.85, 1.10),
        "momentum": _clamp(momentum_mult, 0.85, 1.12),
        "location": _clamp(location_mult, 0.88, 1.08),
        "volume": _clamp(volume_mult, 0.85, 1.08),
    }


def _volatility_mult(snap: Mapping[str, Any]) -> float:
    """Headline volatility multiplier retained for diagnostics/backward compat."""
    mults = _component_vol_mults(snap)
    return round(sum(mults.values()) / len(mults), 4)


# ── volume / flow ────────────────────────────────────────────────────────────
def _volume_component(
    snap: Mapping[str, Any], candles: list[dict], context: Mapping[str, Any] | None
) -> Component:
    signal = 0.0
    quality = 0.0
    valid = [c for c in candles[-20:] if _f(c.get("close")) is not None and (_f(c.get("vol")) or 0) > 0]
    if len(valid) >= 5:
        obv = 0.0
        midpoint = len(valid) // 2
        mid_obv = 0.0
        for index in range(1, len(valid)):
            close = float(valid[index]["close"])
            previous = float(valid[index - 1]["close"])
            volume = float(valid[index].get("vol") or 0)
            obv += volume if close > previous else -volume if close < previous else 0.0
            if index == midpoint:
                mid_obv = obv
        delta = obv - mid_obv
        signal = 1.0 if delta > 0 else -1.0 if delta < 0 else 0.0
        quality = min(1.0, abs(delta) / max(1.0, sum(float(c.get("vol") or 0) for c in valid[midpoint:])))

    # Relative volume: prefer feed-provided ratio (Bybit crypto / EOD stock /
    # Dukascopy forex), else derive from candle volume.
    vr = _f((context or {}).get("volume_ratio"))
    if vr is None and candles:
        vols = [_f(c.get("vol")) for c in candles[-21:]]
        vols = [v for v in vols if v]
        if len(vols) >= 5:
            avg = sum(vols[:-1]) / max(1, len(vols) - 1)
            if avg > 0:
                vr = vols[-1] / avg
    if vr is not None:
        quality = max(quality, _clamp01((vr - 1.0) / 1.5))
        if vr > 1.0 and len(valid) >= 2:
            last_close = float(valid[-1]["close"])
            prev_close = float(valid[-2]["close"])
            bar_dir = (
                1.0 if last_close > prev_close else -1.0 if last_close < prev_close else 0.0
            )
            surprise = bar_dir * _clamp01((vr - 1.0) / 1.5)
            if signal == 0.0:
                signal = surprise
            elif signal * surprise < 0:
                signal = _clamp(signal * 0.6 + surprise * 0.4, -1.0, 1.0)
    return Component(signal, _clamp01(quality), available=len(valid) >= 5)


# ── config resolvers ─────────────────────────────────────────────────────────
def _snapshots(
    candles: dict[str, list[dict]], asset_type: str, periods: Mapping[str, int],
    snapshot_cache: dict | None = None,
) -> dict[str, Mapping[str, Any]]:
    from engine_a_v3.indicator_adapter import indicator_snapshot

    snaps: dict[str, Mapping[str, Any]] = {}
    for tf in ("D1", "H4", "H1"):
        rows = candles.get(tf) or []
        if snapshot_cache is not None:
            key = (tf, len(rows))
            cached = snapshot_cache.get(key)
            if cached is not None:
                snaps[tf] = cached
                continue
        snap = indicator_snapshot(rows, periods, asset_type)
        if snapshot_cache is not None:
            snapshot_cache[(tf, len(rows))] = snap
        snaps[tf] = snap
    return snaps


# ── public entry point ───────────────────────────────────────────────────────
def score_pair(
    route: Any,
    horizon: str,
    candles: dict[str, list[dict]],
    *,
    context: Mapping[str, Any] | None = None,
    profile: Any | None = None,
    snapshot_cache: dict | None = None,
) -> QuantScore:
    """Continuous quality score for one pair. `route` is a SpecialistRoute
    (.score_group, .family). `context` carries subsystem snapshots and an
    optional 'volume_ratio'."""
    group = getattr(route, "score_group", "unknown")
    family = getattr(route, "family", "unknown")
    asset_type = _FAMILY_ASSET.get(family, "other")
    horizon = "intraday" if str(horizon).lower() == "intraday" else "swing"
    entry_tf = _resolve_v3_entry_tf(group, asset_type, horizon)

    if profile is None:
        from engine_a_v3.profile import baseline_profile
        profile = baseline_profile(group, horizon)
    snaps = _snapshots(candles, asset_type, dict(profile.indicator_periods), snapshot_cache)
    entry_snap = snaps.get(entry_tf) or snaps.get("H4") or snaps.get("D1") or {}
    momentum_tf = _resolve_v3_momentum_tf(group, asset_type, horizon)
    momentum_snap = snaps.get(momentum_tf) or snaps.get("H4") or entry_snap

    trend, coherence = _trend_component(
        snaps,
        _resolve_v3_tf_weights(group, asset_type, horizon),
        entry_candles=candles.get(entry_tf) or [],
        indicator_periods=dict(profile.indicator_periods),
        entry_tf=entry_tf,
    )
    momentum, mom_diag = _momentum_component(momentum_snap, asset_type, group)
    if mom_diag.get("adxHardAbort") and mom_diag.get("adxAbortReason") == "missing_both_abort":
        return QuantScore(
            direction="FLAT",
            confluence_score=0.0,
            max_score=MAX_SCORE,
            score_norm=0.0,
            conviction=0.0,
            decision="WATCH",
            threshold=profile.trade_threshold,
            level_style="trend",
            factor_scores={"trend": 0.0, "momentum": 0.0, "ortho": {}},
            factor_diagnostics={
                **mom_diag,
                "adxGateRejected": True,
            },
            components={"trend": trend, "momentum": momentum, "location": Component(0.0, 0.0), "volume": Component(0.0, 0.0)},
        )
    location, level_style = _location_component(entry_snap, asset_type, group)
    volume = _volume_component(entry_snap, candles.get(entry_tf) or [], context)

    components: dict[str, Component] = {
        "trend": trend,
        "momentum": momentum,
        "location": location,
        "volume": volume,
    }
    subsystem_states: dict[str, str] = {}
    if subsystems_enabled():
        sub_weights = resolve_subsystem_weights(family)
        for name in SUBSYSTEM_FACTORS:
            comp, state = subsystem_component((context or {}).get(name))
            components[name] = comp
            subsystem_states[name] = state

    weights = dict(profile.weights)
    combined_weights = dict(weights)
    if subsystems_enabled():
        sub_budget = sum(
            resolve_subsystem_weights(family).get(name, 0.0)
            for name in SUBSYSTEM_FACTORS
            if subsystem_states.get(name) not in {ST_NA, None}
        )
        sub_budget = min(0.35, sub_budget)
        price_scale = max(0.65, 1.0 - sub_budget)
        for name in CORE_COMPONENTS:
            combined_weights[name] = weights.get(name, 0.0) * price_scale
        for name in SUBSYSTEM_FACTORS:
            if subsystem_states.get(name) == ST_NA:
                combined_weights[name] = 0.0
            else:
                combined_weights[name] = resolve_subsystem_weights(family).get(name, 0.0)

    active = {
        name: comp
        for name, comp in components.items()
        if name in CORE_COMPONENTS
        and comp.available
        and (name != "volume" or comp.quality > 0.0 or comp.signal != 0.0)
    }
    weight_sum = 0.0
    for name in active:
        weight_sum += max(0.0, combined_weights.get(name, 0.0))
    if subsystems_enabled():
        for name in SUBSYSTEM_FACTORS:
            w = max(0.0, combined_weights.get(name, 0.0))
            if w > 0 and subsystem_states.get(name) != ST_NA:
                weight_sum += w
    weight_sum = weight_sum or 1.0

    def _subsystem_contributes(name: str) -> bool:
        state = subsystem_states.get(name)
        return state in (ST_AVAILABLE, ST_NEUTRAL) and max(0.0, combined_weights.get(name, 0.0)) > 0

    # Confluence uses the full configured weight budget so unavailable components
    # cannot inflate scores by shrinking the divisor (L-1).
    confluence_denom = sum(
        max(0.0, combined_weights.get(name, 0.0)) for name in CORE_COMPONENTS
    )
    if subsystems_enabled():
        for name in SUBSYSTEM_FACTORS:
            if _subsystem_contributes(name):
                confluence_denom += max(0.0, combined_weights.get(name, 0.0))
    confluence_denom = confluence_denom or 1.0

    # Direction = sign of the weighted directional sum over active components,
    # except in mean-reversion regime where the fade direction is authoritative.
    dir_terms: list[tuple[float, float]] = []
    for n, c in active.items():
        dir_terms.append((combined_weights.get(n, 0.0), c.signal * c.quality))
    if subsystems_enabled():
        for n in SUBSYSTEM_FACTORS:
            if _subsystem_contributes(n):
                comp = components[n]
                dir_terms.append((combined_weights.get(n, 0.0), comp.signal * comp.quality))
    dir_sum = sum(w * term for w, term in dir_terms) / weight_sum
    if level_style == "mean_reversion" and location.signal != 0.0:
        direction = "LONG" if location.signal > 0 else "SHORT"
        dsign = 1.0 if location.signal > 0 else -1.0
    elif dir_sum > profile.direction_deadband:
        direction, dsign = "LONG", 1.0
    elif dir_sum < -profile.direction_deadband:
        direction, dsign = "SHORT", -1.0
    else:
        direction, dsign = "FLAT", 0.0

    # Confluence = weighted aligned quality. Components that disagree with the
    # chosen direction contribute nothing (continuous), but never veto.
    # Dedicated Trend/Momentum boxes + the rest as "orthogonal" factor boxes,
    # matching the cockpit card. Each box shows signed strength (signal*quality).
    factor_scores: dict[str, Any] = {}
    ortho: dict[str, float] = {}
    score_frac = 0.0
    vol_mults = _component_vol_mults(entry_snap)
    for name, comp in components.items():
        weight = max(0.0, combined_weights.get(name, 0.0))
        in_direction_pool = name in active or (subsystems_enabled() and _subsystem_contributes(name))
        aligned = bool(dsign) and (comp.signal * dsign) > 0.0
        vol_mult = vol_mults.get(name, 1.0) if name in CORE_COMPONENTS else 1.0
        if in_direction_pool and aligned:
            score_frac += weight * comp.quality * vol_mult
        signed = round(comp.signal * comp.quality, 4)
        if name in ("trend", "momentum"):
            factor_scores[name] = signed
        elif name in CORE_COMPONENTS:
            ortho[name] = signed
        else:
            ortho[name] = signed
    score_frac /= confluence_denom

    vol_mult = _volatility_mult(entry_snap)
    confluence = _clamp(MAX_SCORE * score_frac, 0.0, MAX_SCORE)
    score_norm = confluence / MAX_SCORE
    threshold = profile.trade_threshold
    decision = "TRADE" if direction in ("LONG", "SHORT") and confluence >= threshold else "WATCH"

    factor_scores["ortho"] = ortho
    factor_scores["ortho_term"] = round(sum(ortho.values()), 4)
    factor_diagnostics = {
        "adxValue": mom_diag.get("adxValue"),
        "adxMultiplier": mom_diag.get("adxMultiplier"),
        "diAlignMult": mom_diag.get("diAlignMult"),
        "directionalRampMult": round(0.5 + 0.5 * abs(dir_sum), 4),
        "mrAdxCeiling": round(_resolve_mr_adx_ceiling(asset_type, group), 4) if level_style == "mean_reversion" else None,
        "trendCoherence": coherence or {"error": "no_ema_data"},
        "volatilityMult": round(vol_mult, 4),
        "componentVolMults": {k: round(v, 4) for k, v in vol_mults.items()},
        "atrPct": _f(entry_snap.get("atr_pct")),
        "subsystemsEnabled": subsystems_enabled(),
        "subsystemStates": subsystem_states or None,
        "components": {
            name: {
                "signal": round(comp.signal, 4), "quality": round(comp.quality, 4),
                "weight": round(combined_weights.get(name, 0.0), 4),
                "contribution": round(
                    combined_weights.get(name, 0.0) * comp.quality * vol_mults.get(name, 1.0)
                    if (dsign and comp.signal * dsign > 0.0)
                    else 0.0,
                    4,
                ),
                "available": comp.available,
            }
            for name, comp in components.items()
        },
    }

    return QuantScore(
        direction=direction,
        confluence_score=round(confluence, 4),
        max_score=MAX_SCORE,
        score_norm=round(score_norm, 4),
        conviction=round(score_norm, 4),
        decision=decision,
        threshold=threshold,
        level_style=level_style,
        factor_scores=factor_scores,
        factor_diagnostics=factor_diagnostics,
        components=components,
    )
