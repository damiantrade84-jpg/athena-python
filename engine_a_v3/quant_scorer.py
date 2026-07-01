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
  enforces this. Subsystem signals (intermarket/carry/sentiment/macro/
  microstructure) are NOT wired in; see
  athena_research/engine_a_ablation/shadow_scorer.py for the research-only
  ablation studying whether/how to add them, gated on the same walk-forward
  evidence engine_a_v3.promotion requires before any live wiring.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from factor_scoring import _adx_multiplier_from_value, _resolve_adx_thresholds
from engine_a_scoring_profile import resolve_engine_a_scoring_profile


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
def _tf_trend(snap: Mapping[str, Any]) -> tuple[float, str]:
    """One timeframe's EMA-stack vote -> (-1..1, label)."""
    close = _f(snap.get("close"))
    e_trend = _f(snap.get("ema21"))   # trend EMA (group period)
    e_mom = _f(snap.get("ema50"))     # momentum EMA
    e_long = _f(snap.get("ema200"))   # long EMA
    if close is None or e_trend is None or e_mom is None or e_long is None:
        return 0.0, "FLAT"
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
    return score, label


def _trend_component(
    snaps: dict[str, Mapping[str, Any]], tf_w: Mapping[str, float]
) -> tuple[Component, dict[str, str]]:
    parts: dict[str, float] = {}
    coherence: dict[str, str] = {}
    weighted = 0.0
    total_w = 0.0
    for tf, w in tf_w.items():
        snap = snaps.get(tf)
        if not snap:
            continue
        score, label = _tf_trend(snap)
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
    return Component(_clamp(weighted, -1.0, 1.0), _clamp01(quality)), coherence


# ── momentum (RSI / MACD / DI+ADX) ───────────────────────────────────────────
def _momentum_component(
    snap: Mapping[str, Any], asset_type: str, score_group: str
) -> tuple[Component, dict[str, Any]]:
    diag: dict[str, Any] = {"adxValue": None, "adxMultiplier": None, "diAlignMult": None}
    if not snap:
        return Component(0.0, 0.0), diag
    sigs: list[float] = []

    rsi = _f(snap.get("rsi"))
    if rsi is not None:
        sigs.append(_clamp((rsi - 50.0) / 30.0, -1.0, 1.0))  # 20/80 saturate

    di_p = _f(snap.get("plusDI"))
    di_m = _f(snap.get("minusDI"))
    di_align = 0.0
    if di_p is not None and di_m is not None and (di_p + di_m) > 0:
        di_align = _clamp((di_p - di_m) / (di_p + di_m), -1.0, 1.0)
        sigs.append(di_align)
        diag["diAlignMult"] = round(1.0 + 0.3 * di_align, 4)

    hist = _f(snap.get("macdHist"))
    hist_prev = _f(snap.get("macdHistPrev"))
    if hist is not None:
        base = 1.0 if hist > 0 else -1.0 if hist < 0 else 0.0
        if hist_prev is not None and base != 0.0:
            rising = hist > hist_prev
            strengthening = (base > 0 and rising) or (base < 0 and not rising)
            base *= 1.0 if strengthening else 0.6
        elif base != 0.0:
            base *= 0.8
        sigs.append(base)

    signal = sum(sigs) / len(sigs) if sigs else 0.0

    adx = _f(snap.get("adx"), 0.0) or 0.0
    diag["adxValue"] = round(adx, 2)
    # ADX gates how *meaningful* momentum is (quality), never direction. Thresholds
    # are per-group from ADX_TREND_MIN_CLASS / FACTOR_ADX_HARD_FAIL_CLASS (canonical
    # factor_scoring resolver) so forex/crypto/etc. use their configured trend_min
    # and hard_fail instead of a uniform hardcoded 25/30. adx_mult ramps 0 below
    # hard_fail → 1 at trend_min (matches factor_scoring's _adx_multiplier_from_value).
    trend_min, hard_fail = _resolve_adx_thresholds(asset_type, score_group)
    adx_mult = _adx_multiplier_from_value(adx, trend_min, hard_fail)
    diag["adxMultiplier"] = round(adx_mult, 4)
    quality = _clamp01(abs(signal) * adx_mult)
    return Component(_clamp(signal, -1.0, 1.0), quality), diag


# ── location (pullback timing / mean-reversion regime) ───────────────────────
def _location_component(snap: Mapping[str, Any]) -> tuple[Component, str]:
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

    if adx < 18.0 and stretched:
        # Range fade: signal opposite to the stretch; quality scales with stretch.
        signal = -1.0 if (bb_u is not None and close > bb_u) else 1.0
        quality = _clamp01(abs(dist) / 3.0)
        return Component(signal, quality), "mean_reversion"

    # Trend timing: quality peaks near the EMA, decays with extension.
    extension = abs(dist)
    quality = _clamp01(1.0 - max(0.0, extension - 0.5) / 3.0)
    signal = _clamp(dist / 2.0, -0.5, 0.5)  # mild directional bias only
    return Component(signal, quality), "trend"


# ── volatility regime (quality multiplier, not directional) ──────────────────
def _volatility_mult(snap: Mapping[str, Any]) -> float:
    if not snap:
        return 1.0
    mult = 1.0
    adx_pct = _f(snap.get("adx_pct"))
    if adx_pct is not None:
        mult += 0.10 * (adx_pct - 0.5)        # trending percentile -> up to +/-0.05
    atr_pct = _f(snap.get("atr_pct"))
    if atr_pct is not None and atr_pct > 0.9:
        mult -= 0.10                          # blow-off volatility -> de-rate
    return _clamp(mult, 0.85, 1.10)


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
    entry_tf = "H1" if horizon == "intraday" else "H4"

    if profile is None:
        from engine_a_v3.profile import baseline_profile
        profile = baseline_profile(group, horizon)
    snaps = _snapshots(candles, asset_type, dict(profile.indicator_periods), snapshot_cache)
    entry_snap = snaps.get(entry_tf) or snaps.get("H4") or snaps.get("D1") or {}
    momentum_tf = _resolve_v3_momentum_tf(group, asset_type, horizon)
    momentum_snap = snaps.get(momentum_tf) or snaps.get("H4") or entry_snap

    trend, coherence = _trend_component(snaps, _resolve_v3_tf_weights(group, asset_type, horizon))
    momentum, mom_diag = _momentum_component(momentum_snap, asset_type, group)
    location, level_style = _location_component(entry_snap)
    volume = _volume_component(entry_snap, candles.get(entry_tf) or [], context)

    components: dict[str, Component] = {
        "trend": trend,
        "momentum": momentum,
        "location": location,
        "volume": volume,
    }
    weights = dict(profile.weights)
    # Normalize over ACTIVE components only — a subsystem with no data (neutral)
    # must neither inject noise nor dilute the score (no-veto, no false penalty).
    active = {
        name: comp
        for name, comp in components.items()
        if comp.available and (name != "volume" or comp.quality > 0.0 or comp.signal != 0.0)
    } or components
    weight_sum = sum(max(0.0, weights.get(name, 0.0)) for name in active) or 1.0

    # Direction = sign of the weighted directional sum over active components.
    dir_sum = sum(weights.get(n, 0.0) * c.signal * c.quality for n, c in active.items()) / weight_sum
    if dir_sum > profile.direction_deadband:
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
    for name, comp in components.items():
        weight = max(0.0, weights.get(name, 0.0))
        # Aligned quality: a component that agrees with the chosen direction
        # contributes its full quality; one that disagrees contributes nothing
        # (no veto, but it still dilutes via weight_sum). comp.quality already
        # encodes each component's directional magnitude/confidence, so we gate
        # by sign rather than re-multiplying by signal magnitude — the latter
        # double-counted magnitude (e.g. momentum -> signal**2) and capped the
        # realized confluence well below MAX_SCORE.
        aligned = bool(dsign) and (comp.signal * dsign) > 0.0
        if name in active and aligned:
            score_frac += weight * comp.quality
        signed = round(comp.signal * comp.quality, 4)
        if name in ("trend", "momentum"):
            factor_scores[name] = signed
        else:
            ortho[name] = signed
    score_frac /= weight_sum

    vol_mult = _volatility_mult(entry_snap)
    confluence = _clamp(MAX_SCORE * score_frac * vol_mult, 0.0, MAX_SCORE)
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
        "trendCoherence": coherence or {"error": "no_ema_data"},
        "volatilityMult": round(vol_mult, 4),
        "components": {
            name: {
                "signal": round(comp.signal, 4), "quality": round(comp.quality, 4),
                "weight": round(weights.get(name, 0.0), 4),
                "contribution": round(weights.get(name, 0.0) * comp.quality if (dsign and comp.signal * dsign > 0.0) else 0.0, 4),
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
