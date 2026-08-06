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

import logging

from factor_scoring import _adx_multiplier_from_value, _resolve_adx_thresholds
from engine_a_scoring_profile import (
    default_trend_tf_weights,
    resolve_engine_a_scoring_profile,
)

log = logging.getLogger("athena.quant_scorer")
from engine_a_v3.profile import CORE_COMPONENTS
from engine_a_v3.timeframes import (
    resolve_diagnostic_v3_entry_timeframe,
    resolve_v3_entry_timeframe,
)
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
# The fallback is derived from engine_a_scoring_profile defaults so a disabled
# or erroring profile cannot silently switch scoring to a divergent hardcoded
# table (the previous _TF_WEIGHTS disagreed with the profile defaults).
def _fallback_tf_weights(horizon: str) -> dict[str, float]:
    try:
        weights = default_trend_tf_weights(horizon)
        if weights:
            return weights
    except Exception:
        pass
    return (
        {"D1": 0.50, "H4": 0.30, "H1": 0.20}
        if horizon == "swing"
        else {"D1": 0.42, "H4": 0.33, "H1": 0.25}
    )


# Legacy policy-role trend weights (structure heaviest, regime lightest).
# Retained only for ENGINE_A_POLICY_TREND_WEIGHTS_FROM_PROFILE=false rollback:
# this table inverts every configured stack (all of which are D1-led — see
# ENGINE_A_SCORING_PROFILE trend_weights) and discards per-group weighting
# entirely, so the profile-derived path below is the default.
_POLICY_ROLE_TF_WEIGHTS: tuple[tuple[str, str, float], ...] = (
    ("regime", "regimeTf", 0.18),
    ("bias", "biasTf", 0.37),
    ("structure", "structureTf", 0.45),
)

# Slow-to-fast trend roles. The policy owns which timeframe fills each slot;
# ENGINE_A_SCORING_PROFILE owns how heavily each slot is weighted.
_POLICY_TREND_ROLES: tuple[tuple[str, str], ...] = (
    ("regime", "regimeTf"),
    ("bias", "biasTf"),
    ("structure", "structureTf"),
)


def _policy_trend_weights_from_profile(
    policy: Mapping[str, Any],
    score_group: str,
    asset_type: str,
    horizon: str,
) -> tuple[dict[str, float], dict[str, Any]]:
    """Map the policy's trend-role timeframes onto the configured trend weights.

    ``ENGINE_A_SCORING_PROFILE.trend_layers`` is an ordered slow-to-fast stack
    (D1/H4/H1 by default) with a ``weight_key`` per layer; ``trend_weights``
    carries the per-group/per-style weight for each key. The policy replaces the
    *timeframe* in each slot (e.g. a broad cross reads H4 where a major reads
    H1) without replacing the calibrated *weight* of that slot.

    Roles that resolve to the same timeframe have their weights summed, so a
    profile whose bias and structure rungs coincide reads that timeframe once
    with the combined weight. The collapse is reported in the returned
    diagnostics rather than being silent.
    """
    layers = []
    weights: Mapping[str, Any] = {}
    try:
        profile_cfg = resolve_engine_a_scoring_profile(
            score_group=score_group, asset_type=asset_type, style=horizon
        )
        if profile_cfg.get("enabled"):
            layers = list(profile_cfg.get("trend_layers") or [])
            weights = profile_cfg.get("trend_weights") or {}
    except Exception:
        layers = []
    ordered_weights: list[float] = []
    for layer in layers:
        wkey = str(layer.get("weight_key", "")).strip()
        if not wkey or wkey not in weights:
            continue
        try:
            ordered_weights.append(float(weights[wkey]))
        except (TypeError, ValueError):
            continue
    layer_count_mismatch = len(ordered_weights) != len(_POLICY_TREND_ROLES)
    if layer_count_mismatch:
        # Profile disabled/incomplete: fall back to the style defaults, which
        # are the same source `_fallback_tf_weights` uses. Never fall through
        # to the legacy inverted role table. This silently discards a group's
        # configured stack, so it is reported rather than swallowed — every
        # group in ENGINE_A_SCORING_PROFILE currently declares three layers, and
        # a two-layer stack would otherwise be scored with style defaults.
        fallback = _fallback_tf_weights(horizon)
        ordered_weights = [
            float(fallback.get(tf, 0.0)) for tf in ("D1", "H4", "H1")
        ]
    tf_weights: dict[str, float] = {}
    role_map: dict[str, str] = {}
    role_weights: dict[str, float] = {}
    for (key, alias), weight in zip(_POLICY_TREND_ROLES, ordered_weights):
        tf = str(policy.get(key) or policy.get(alias) or "").upper()
        if not tf:
            continue
        role_map[key] = tf
        role_weights[key] = round(float(weight), 4)
        tf_weights[tf] = tf_weights.get(tf, 0.0) + weight
    diagnostics = {
        "trendWeightSource": (
            "policy_roles_style_fallback"
            if layer_count_mismatch
            else "policy_roles_profile_weights"
        ),
        "trendRoleTimeframes": dict(role_map),
        # Profile weights are calibrated by timeframe name but applied by role
        # position, so a policy promotion redirects a calibrated weight onto a
        # different timeframe. Emit the resolved (role, tf, weight) triple.
        "trendRoleWeights": dict(role_weights),
        "trendLayersCollapsed": len(role_map) - len(tf_weights),
        "trendLayerCountMismatch": layer_count_mismatch,
    }
    return tf_weights, diagnostics


def _resolve_v3_tf_weights(score_group: str, asset_type: str, horizon: str) -> dict[str, float]:
    """Resolve per-group TF weights for the V3 trend component.

    Wires V3 to ``ENGINE_A_SCORING_PROFILE`` so per-group TF overrides take live
    effect (e.g., energy_oil/commodity_other drop D1 and weight H4+H1 0.55/0.45;
    us_stock_single uses 0.40/0.35/0.25). The profile's ``trend_layers`` map each
    ``weight_key`` to a ``tf``; the returned dict is keyed by TF (D1/H4/H1) so
    ``_trend_component`` can consume it directly. Falls back to the hardcoded
    ``_fallback_tf_weights(horizon)`` when the profile is disabled or yields no usable
    weights, preserving fail-closed behaviour for unaudited groups / disabled config.
    """
    try:
        profile = resolve_engine_a_scoring_profile(
            score_group=score_group, asset_type=asset_type, style=horizon
        )
    except Exception:
        return _fallback_tf_weights(horizon)
    if not profile.get("enabled"):
        return _fallback_tf_weights(horizon)
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
    return tf_weights or _fallback_tf_weights(horizon)


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


def _policy_trend_weights_from_profile_enabled() -> bool:
    try:
        from config import CONFIG

        return bool(
            CONFIG.get("ENGINE_A_POLICY_TREND_WEIGHTS_FROM_PROFILE", True)
        )
    except Exception:
        return True


def _policy_momentum_anchor_mode() -> str:
    """``profile`` (default) or ``policy_trigger`` (legacy v4 behaviour)."""
    try:
        from config import CONFIG

        mode = str(CONFIG.get("ENGINE_A_MOMENTUM_ANCHOR", "profile") or "profile")
    except Exception:
        mode = "profile"
    mode = mode.strip().lower()
    return mode if mode in {"profile", "policy_trigger"} else "profile"


def _resolve_policy_momentum_tf(
    score_group: str,
    asset_type: str,
    horizon: str,
    policy_trigger_tf: str,
) -> str:
    """Resolve the momentum anchor timeframe under an authoritative policy.

    Default (``ENGINE_A_MOMENTUM_ANCHOR=profile``) keeps the configured
    ``ENGINE_A_SCORING_PROFILE.momentum_tf`` anchor — H4 for most groups, D1 for
    bond_tlt. Anchoring momentum on the policy *trigger* rung instead pushed
    RSI/MACD/DI and the ADX hard-abort gate onto M5/M15 for a large slice of the
    universe, which contradicts the profile's documented per-group tuning; the
    trigger rung's momentum is still reported separately as ``triggerEvidence``.
    ``policy_trigger`` restores the previous behaviour.
    """
    try:
        profile = resolve_engine_a_scoring_profile(
            score_group=score_group, asset_type=asset_type, style=horizon
        )
    except Exception:
        return policy_trigger_tf
    if not profile.get("enabled"):
        return policy_trigger_tf
    if _policy_momentum_anchor_mode() == "policy_trigger":
        return str(profile.get("group_momentum_tf") or policy_trigger_tf).upper()
    anchor = str(
        profile.get("group_momentum_tf") or profile.get("momentum_tf") or ""
    ).upper()
    return anchor or policy_trigger_tf


def _resolve_v3_entry_tf(score_group: str, asset_type: str, horizon: str) -> str | None:
    """Resolve primary entry TF for V3 scoring.

    Default remains H1 (intraday) / H4 (swing). Only a style-nested score-group
    override may change the quant entry bar; flat group overrides are ignored so
    they cannot collapse both styles onto one timeframe.
    """
    return resolve_v3_entry_timeframe(score_group, asset_type, horizon)


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
    # Directional components vote on LONG/SHORT and are credited only when their
    # sign matches the chosen direction. Non-directional components measure
    # *quality of the moment* rather than side (trend-mode location: how close
    # price sits to the trend EMA), so they neither vote on direction nor get
    # zeroed for disagreeing with it — see `_location_component`.
    directional: bool = True


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
    # Label on the majority margin, not a fixed 0.34 cut. The vote count varies
    # (3 without an ema200 slope, 4 with one), so a fixed cut labelled a 2-1
    # majority (0.333) FLAT while labelling the equivalent 3-1 majority (0.5) UP
    # — the trend label flipped on whether a slope value happened to exist.
    # `score` is a mean of +/-1 votes, so any net majority is >= 1/len(votes).
    margin = (1.0 / len(votes)) - 1e-9
    label = "UP" if score >= margin else "DOWN" if score <= -margin else "FLAT"
    return score, label, True


def _trend_component(
    snaps: dict[str, Mapping[str, Any]],
    tf_w: Mapping[str, float],
    *,
    entry_candles: list[dict] | None = None,
    indicator_periods: Mapping[str, int] | None = None,
    entry_tf: str = "H1",
    series_cache=None,
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
        weighted, snaps, entry_candles, indicator_periods, entry_tf=entry_tf,
        series_cache=series_cache,
    )
    return Component(_clamp(weighted, -1.0, 1.0), _clamp01(quality)), coherence


def _trend_alignment_age(
    candles: list[dict],
    *,
    ema_period: int = 21,
    max_lookback: int = 25,
    series_cache=None,
    timeframe: str | None = None,
) -> int:
    """Count consecutive entry-TF bars with the same close vs EMA side as the latest bar."""
    if len(candles) < ema_period + 2:
        return 0
    from engine_a_v3.setups import _ema

    if (
        series_cache is not None
        and timeframe is not None
        and series_cache.matches(timeframe, candles)
    ):
        ema = series_cache.ema_series_view(timeframe, len(candles), ema_period)
        if ema is not None:
            latest = len(candles) - 1
            latest_side = float(candles[latest]["close"]) > ema[latest]
            age = 0
            start = latest - 1
            stop = max(ema_period - 1, start - max_lookback)
            for idx in range(start, stop, -1):
                if (float(candles[idx]["close"]) > ema[idx]) == latest_side:
                    age += 1
                else:
                    break
            return age

    closes = [float(c["close"]) for c in candles if _f(c.get("close")) is not None]
    if len(closes) < ema_period + 2:
        return 0
    ema = None
    if (
        series_cache is not None
        and timeframe is not None
        and len(closes) == len(candles)
        and series_cache.matches(timeframe, candles)
    ):
        # EMA is causal, so the full-series precompute equals the per-prefix
        # array elementwise for every index < len(candles).
        ema = series_cache.ema_series_view(timeframe, len(candles), ema_period)
    if ema is None:
        ema = _ema(closes, ema_period)
    # Index explicitly: the cached view is the FULL-series array, valid only
    # for indices < len(closes); ema[-1] would read beyond the prefix.
    latest_side = closes[-1] > ema[len(closes) - 1]
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
    series_cache=None,
) -> float:
    """Penalize stale or weakening trends (config-gated, default on)."""
    enabled = True
    start_bars = 8
    bar_penalty = 0.03
    floor = 0.75
    adx_weakening_penalty = 0.15
    plateau_enabled = False
    plateau_mult = 0.85
    try:
        from config import CONFIG

        cfg = CONFIG.get("ENGINE_A_V3_TREND_HEALTH") or {}
        enabled = bool(cfg.get("ENABLED", True))
        start_bars = int(cfg.get("STALE_ALIGNMENT_BARS", 8))
        bar_penalty = float(cfg.get("BAR_PENALTY", 0.03))
        floor = float(cfg.get("FLOOR", 0.75))
        adx_weakening_penalty = float(cfg.get("ADX_WEAKENING_PENALTY", 0.15))
        plateau_enabled = bool(cfg.get("PLATEAU_PENALTY_ENABLED", False))
        plateau_mult = float(cfg.get("PLATEAU_PENALTY_MULT", 0.85))
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
    # ADX is unsigned trend strength: a falling slope means the trend is
    # weakening regardless of direction, so the penalty applies symmetrically
    # to LONG and SHORT (previously the SHORT branch penalized rising ADX —
    # i.e. strengthening downtrends — inverting the intent for shorts).
    adx_slope = _f(entry_snap.get("adxSlope"), 0.0) or 0.0
    if adx_slope < 0:
        mult *= _clamp(1.0 + adx_weakening_penalty * adx_slope, floor, 1.0)

    # Plateau penalty is off by default: a steady ADX above 25 is a stable
    # trend, not a stale one — actual weakening is the negative-slope branch
    # above. Re-enable via ENGINE_A_V3_TREND_HEALTH.PLATEAU_PENALTY_ENABLED.
    adx = _f(entry_snap.get("adx"), 0.0) or 0.0
    adx_prev = _f(entry_snap.get("adxPrev"))
    if plateau_enabled and adx_prev is not None and adx > 25 and abs(adx - adx_prev) < 1.0:
        mult *= plateau_mult

    if entry_candles:
        ema_period = 21
        if indicator_periods and "ema_trend" in indicator_periods:
            ema_period = int(indicator_periods["ema_trend"])
        age = _trend_alignment_age(
            entry_candles,
            ema_period=ema_period,
            series_cache=series_cache,
            timeframe=_entry_key,
        )
        if age > start_bars:
            mult *= max(floor, 1.0 - bar_penalty * (age - start_bars))
    return _clamp(mult, floor, 1.0)


def _group_scoped_blend_weight(
    cfg: Mapping[str, Any], score_group: str, key: str, fallback: float
) -> float:
    """Resolve a Tuning Lab indicator-blend weight with an optional per-group
    override before falling back to the process-wide value.

    Lookup order: ``cfg["BY_GROUP"][score_group][key]`` -> ``cfg[key]`` ->
    ``fallback``. A group with no ``BY_GROUP`` entry gets exactly the
    process-wide weight (today, 0.0 for every knob this feeds), so scoring for
    every other group is unaffected by a group-specific tune-and-push.
    """
    by_group = cfg.get("BY_GROUP")
    if isinstance(by_group, Mapping):
        group_row = by_group.get(str(score_group or "").strip().lower())
        if isinstance(group_row, Mapping) and key in group_row:
            try:
                return float(group_row[key])
            except (TypeError, ValueError):
                pass
    try:
        return float(cfg.get(key, fallback))
    except (TypeError, ValueError):
        return fallback


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
        "rocTerm": None,
    }
    if not snap:
        return Component(0.0, 0.0), diag

    rsi_w, di_w, macd_w = 0.35, 0.35, 0.30
    # Tuning Lab (athena_experiment) extra momentum indicator: ROC. Default
    # 0.0 — inert unless a group's config.local.yaml explicitly weights it in.
    # Stoch/CCI/Williams %R terms were removed 2026-08-05: W%R is raw Stoch %K
    # − 100 over the same window, all three duplicate RSI's bounded-oscillator
    # shape, and their linear pro-extreme scoring rewarded entries at maximum
    # overbought/oversold in a direction-credited blend.
    roc_w = 0.0
    if _momentum_blend_enabled():
        try:
            from config import CONFIG

            cfg = CONFIG.get("ENGINE_A_V3_MOMENTUM_BLEND") or {}
            rsi_w = float(cfg.get("RSI_WEIGHT", 0.35))
            di_w = float(cfg.get("DI_WEIGHT", 0.35))
            macd_w = float(cfg.get("MACD_SLOPE_WEIGHT", 0.30))
            roc_w = _group_scoped_blend_weight(cfg, score_group, "ROC_WEIGHT", 0.0)
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
            # Scale-invariant slope: normalize by the histogram's own magnitude.
            # The previous absolute 0.05 divisor floor + |term|<0.05 snap made the
            # term non-monotonic (hist 1.03 -> 0.8 but 1.06 -> 0.06) and pinned it
            # at a constant +/-0.8 for any instrument whose |hist| < 0.05 (all
            # forex, most commodities), discarding slope information entirely.
            base = max(abs(hist_prev), abs(hist))
            rel_slope = _clamp(slope / base, -1.0, 1.0) if base > 0 else 0.0
            sign_term = 0.8 if hist > 0 else -0.8 if hist < 0 else 0.0
            if sign_term != 0.0:
                rising = hist > hist_prev
                strengthening = (sign_term > 0 and rising) or (sign_term < 0 and not rising)
                macd_term = (
                    _clamp(sign_term + 0.2 * rel_slope, -1.0, 1.0)
                    if strengthening
                    else sign_term * 0.6
                )
            else:
                macd_term = rel_slope
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

    # ── Tuning Lab extra momentum term (ROC) ────────────────────────────────
    # Inert unless ROC_WEIGHT is configured > 0 (see resolution above), so with
    # no config change this block contributes nothing and `signal` below is
    # bit-for-bit the pre-existing RSI/DI/MACD blend.
    if roc_w > 0:
        roc = _f(snap.get("roc"))
        if roc is not None:
            # Scale-invariant normalization: express the ROC move in units of
            # ~3 ATRs of the same snapshot. A fixed 5% divisor saturated the
            # term on high-volatility groups (crypto H4/D1 routinely moves >5%
            # in 12 bars), pinning it at +/-1 and discarding all gradation.
            # Falls back to the fixed divisor when ATR/close are unavailable.
            atr_val = _f(snap.get("atr"))
            close_val = _f(snap.get("close"))
            roc_scale = (
                3.0 * atr_val / close_val * 100.0
                if atr_val and close_val and close_val > 0
                else 5.0
            )
            roc_term = _clamp(roc / roc_scale, -1.0, 1.0)
            weighted_signal += roc_w * roc_term
            weight_total += roc_w
            quality_terms.append(_clamp01(abs(roc) / roc_scale))
            diag["rocTerm"] = round(roc_term, 4)

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
    # ADX slope/decay. legacy_filters reads these off mom_diag to drive the forex
    # EMA-cluster and crypto late-trend adjustments; they were never emitted, so
    # `adx_falling` was hardcoded False downstream and the crypto late-trend
    # penalty could only fire on weak ADX — never on the strong-but-decaying
    # trend it was written for (factor_scoring: adx_weak_or_falling).
    adx_slope = _f(snap.get("adxSlope"))
    adx_prev = _f(snap.get("adxPrev"))
    if adx_slope is None and adx_prev is not None:
        adx_slope = adx - adx_prev
    if adx_slope is not None:
        diag["adxSlope"] = round(adx_slope, 4)
        diag["adxFalling"] = bool(adx_slope < 0)
    trend_min, hard_fail = _resolve_adx_thresholds(asset_type, score_group)
    adx_mult = _adx_multiplier_from_value(adx, trend_min, hard_fail)
    diag["adxMultiplier"] = round(adx_mult, 4)
    if adx_mult <= 0.0:
        diag["adxHardFail"] = True
    base_quality = sum(quality_terms) / len(quality_terms) if quality_terms else 0.0
    quality = _clamp01(base_quality * adx_mult)
    return Component(_clamp(signal, -1.0, 1.0), quality), diag


def _location_trend_timing_only() -> bool:
    """Whether trend-mode location is scored as timing rather than direction.

    Default (true) fixes an alignment asymmetry: trend-mode ``signal`` was
    ``dist/2`` (positive above the trend EMA), and the aggregator only credits
    components whose sign matches the chosen direction. A LONG with price half
    an ATR *above* ema21 was therefore credited in full, while a LONG with price
    half an ATR *below* ema21 — the textbook pullback entry this component was
    written to reward, and the one carrying the higher ``quality`` — contributed
    nothing. Set ``ENGINE_A_V3_LOCATION.TREND_TIMING_ONLY: false`` to restore the
    directional behaviour.
    """
    try:
        from config import CONFIG

        cfg = CONFIG.get("ENGINE_A_V3_LOCATION") or {}
        return bool(cfg.get("TREND_TIMING_ONLY", True))
    except Exception:
        return True


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
def _mean_reversion_requires_higher_tf_confirmation() -> bool:
    """Whether the MR regime must also see a non-trending higher rung.

    The regime switch reads ADX and Bollinger state from the *entry/setup* rung
    and then overrides the multi-timeframe consensus direction outright. Without
    corroboration, a low-ADX pocket on a fast rung (e.g. M30 ADX 15) flipped
    signals whose momentum anchor was genuinely trending (H4 ADX 28 with aligned
    DI) into a counter-trend fade. Reversible:
    ``ENGINE_A_V3_MEAN_REVERSION.REQUIRE_HIGHER_TF_ADX_CONFIRMATION: false``.
    """
    try:
        from config import CONFIG

        cfg = CONFIG.get("ENGINE_A_V3_MEAN_REVERSION") or {}
        return bool(cfg.get("REQUIRE_HIGHER_TF_ADX_CONFIRMATION", True))
    except Exception:
        return True


def _location_component(
    snap: Mapping[str, Any],
    asset_type: str,
    score_group: str,
    *,
    corroborating_adx: float | None = None,
    current_price: float | None = None,
) -> tuple[Component, str]:
    """Entry timing. In a trend, a small pullback toward the trend EMA is the
    best entry (high quality); over-extension lowers quality but never vetoes.
    In a weak-ADX, BB-stretched regime, flips to a mean-reversion fade signal.

    ``corroborating_adx`` is the momentum-anchor rung's ADX. When supplied, the
    mean-reversion regime also requires that rung to be non-trending, so the fade
    cannot be triggered by a fast-rung ADX dip inside a higher-timeframe trend.
    """
    if not snap:
        return Component(0.0, 0.0, available=False), "trend"
    # Keep EMA/ATR/channel state anchored to the confirmed setup candle, but
    # price the entry-location measurement from the freshness-gated scan quote
    # when one is supplied.  Falling back to the confirmed close is reserved
    # for causal/offline callers that do not have a separate quote.
    close = _f(current_price) if current_price is not None else _f(snap.get("close"))
    e_trend = _f(snap.get("ema21"))
    atr = _f(snap.get("atr"))
    if close is None or close <= 0 or e_trend is None or atr is None or atr <= 0:
        return Component(0.0, 0.0, available=False), "trend"
    dist = (close - e_trend) / atr  # >0 above trend EMA
    adx = _f(snap.get("adx"), 0.0) or 0.0
    bb_u = _f(snap.get("bbUpper"))
    bb_l = _f(snap.get("bbLower"))
    stretched = bool((bb_u is not None and close > bb_u) or (bb_l is not None and close < bb_l))
    mr_adx_ceiling = _resolve_mr_adx_ceiling(asset_type, score_group)

    higher_tf_ranging = True
    if corroborating_adx is not None and _mean_reversion_requires_higher_tf_confirmation():
        higher_tf_ranging = corroborating_adx < mr_adx_ceiling

    if adx < mr_adx_ceiling and stretched and higher_tf_ranging:
        # Range fade: signal opposite to the stretch; quality scales with stretch.
        signal = -1.0 if (bb_u is not None and close > bb_u) else 1.0
        quality = _clamp01(abs(dist) / 3.0)
        return Component(signal, quality), "mean_reversion"

    # Trend timing: quality peaks near the EMA, decays with extension. The
    # signed distance is retained for diagnostics/direction only in the legacy
    # mode; by default location is a timing component (see
    # _location_trend_timing_only) so a pullback and an equal-sized extension
    # are scored on the same quality curve instead of the pullback being
    # discarded for "disagreeing" with the trade direction.
    extension = abs(dist)
    quality = _clamp01(1.0 - max(0.0, extension - 0.5) / 3.0)
    signal = _clamp(dist / 2.0, -0.5, 0.5)  # mild directional bias only
    if _location_trend_timing_only():
        return Component(signal, quality, directional=False), "trend"
    return Component(signal, quality), "trend"


# ── volatility regime (per-component quality multipliers) ────────────────────
def _volatility_gating_enabled() -> bool:
    try:
        from config import CONFIG

        cfg = CONFIG.get("ENGINE_A_V3_VOLATILITY_GATING") or {}
        return bool(cfg.get("ENABLED", True))
    except Exception:
        return True


def _volatility_denominator_normalized() -> bool:
    """Whether the per-component volatility multiplier also scales the divisor."""
    try:
        from config import CONFIG

        cfg = CONFIG.get("ENGINE_A_V3_VOLATILITY_GATING") or {}
        return bool(cfg.get("NORMALIZE_DENOMINATOR", True))
    except Exception:
        return True


def _component_vol_mults(snap: Mapping[str, Any], group: str | None = None, asset_type: str | None = None) -> dict[str, float]:
    """Per-component volatility/regime multipliers (default global 1.0).

    When ENGINE_A_V3_GROUP_VOL_BANDS_ENABLED true, ATR% ratio bands (VOLATILITY_SCALER_BANDS)
    drive location/volume (and gentle trend) instead of generic atr_pct>0.9 percentile gate,
    matching factor_scoring._volatility_scaler semantics per group.
    """
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
    # Group-aware ATR% ratio bands (preferred when enabled and group/asset known)
    group_mult_applied = False
    if group is not None and asset_type is not None:
        try:
            from config import CONFIG
            if CONFIG.get("ENGINE_A_V3_GROUP_VOL_BANDS_ENABLED", False):
                from factor_scoring import _resolve_class_keyed
                bands = CONFIG.get("VOLATILITY_SCALER_BANDS") or {}
                band = _resolve_class_keyed(bands, group, asset_type, None)
                if isinstance(band, dict) and "low" in band and "high" in band:
                    atr = _f(snap.get("atr"))
                    close = _f(snap.get("close"))
                    if atr is not None and close and close > 0 and atr > 0:
                        ratio = atr / close
                        low = float(band["low"]); high = float(band["high"])
                        if ratio <= low:
                            g_mult = 1.15
                        elif ratio >= high:
                            g_mult = 0.85
                        else:
                            t = (ratio - low) / (high - low) if high != low else 0.5
                            g_mult = 1.15 + t * (0.85 - 1.15)
                        # Apply group mult to location/volume primarily (precision vs noise)
                        # Blend with generic 0.5-centred logic instead of replacing ADX term.
                        location_mult *= (0.7 + 0.3 * g_mult)
                        volume_mult *= (0.8 + 0.2 * g_mult)
                        trend_mult *= (0.9 + 0.1 * g_mult)
                        group_mult_applied = True
                        # Debug trail via log when needed
                        # log.debug("group vol band %s %s ratio %.5f low %.5f high %.5f gmult %.3f", group, asset_type, ratio, low, high, g_mult)
        except Exception:
            pass
    if not group_mult_applied and atr_pct is not None:
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


def _report_only_group_vol_bands_diagnostic(snap: Mapping[str, Any], group: str, asset_type: str) -> dict:
    """Report-only: what group-aware VOLATILITY_SCALER_BANDS multiplier would be.

    Never changes score; enabled only when ENGINE_A_V3_GROUP_VOL_BANDS_ENABLED true
    and would use per-group low/high instead of generic adx_pct/atr_pct 0.5 center.
    """
    try:
        from config import CONFIG
        if not CONFIG.get("ENGINE_A_V3_GROUP_VOL_BANDS_ENABLED", False):
            return {"enabled": False, "would_apply": False}
        # Resolve per-group bands like factor_scoring._volatility_scaler does
        from factor_scoring import _resolve_class_keyed
        bands = CONFIG.get("VOLATILITY_SCALER_BANDS") or {}
        band = _resolve_class_keyed(bands, group, asset_type, None)
        if not isinstance(band, dict) or "low" not in band or "high" not in band:
            return {"enabled": True, "would_apply": False, "reason": "no_group_band"}
        atr = _f(snap.get("atr"))
        close = _f(snap.get("close"))
        if atr is None or close is None or close <= 0 or atr <= 0:
            return {"enabled": True, "would_apply": False, "reason": "missing_atr_close"}
        atr_pct = atr / close
        low = float(band["low"]); high = float(band["high"])
        # Would-be multiplier on same 0.85-1.15 scale as factor_scoring._volatility_scaler
        if atr_pct <= low:
            would_mult = 1.15
        elif atr_pct >= high:
            would_mult = 0.85
        else:
            t = (atr_pct - low) / (high - low) if high != low else 0.5
            would_mult = 1.15 + t * (0.85 - 1.15)
        return {"enabled": True, "would_apply": True, "atr_pct": round(atr_pct, 6), "band": {"low": low, "high": high}, "would_mult": round(would_mult, 4)}
    except Exception as exc:
        return {"enabled": False, "would_apply": False, "error": str(exc)}


def _report_only_spread_diagnostic(entry_snap: Mapping[str, Any], group: str) -> dict:
    """Report-only spread-to-SL penalty. Never demotes until ENGINE_A_SPREAD_SCORE_PENALTY.ENABLED."""
    try:
        from config import CONFIG
        cfg = CONFIG.get("ENGINE_A_SPREAD_SCORE_PENALTY") or {}
        if not cfg.get("ENABLED", False):
            return {"enabled": False, "would_penalize": False}
        atr = _f(entry_snap.get("atr"))
        if atr is None or atr <= 0:
            return {"enabled": True, "would_penalize": False, "reason": "missing_atr"}
        # Use MAX_EXECUTION_SPREAD_PCT_BY_SYMBOL / MAX_EXECUTION_SPREAD_PCT as proxy when live spread not on snap
        spread_pct = _f(entry_snap.get("spread_pct")) or _f(entry_snap.get("spreadPct"))
        if spread_pct is None:
            return {"enabled": True, "would_penalize": False, "reason": "no_spread_on_snap"}
        ratio = spread_pct * _f(entry_snap.get("close") or 1.0) / atr if atr else 0
        max_ratio = float(cfg.get("MAX_SPREAD_TO_SL_RATIO", 0.20))
        mult = float(cfg.get("PENALTY_MULT", 0.85))
        would = ratio > max_ratio
        return {"enabled": True, "would_penalize": would, "spreadToAtr": round(ratio, 4), "threshold": max_ratio, "would_mult": mult if would else 1.0}
    except Exception as exc:
        return {"enabled": False, "would_penalize": False, "error": str(exc)}


def _report_only_correlation_diagnostic(group: str, family: str) -> dict:
    """Report-only USD-cluster correlation penalty. Never demotes until ENABLED."""
    try:
        from config import CONFIG
        cfg = CONFIG.get("ENGINE_A_CORRELATION_SCORE_PENALTY") or {}
        if not cfg.get("ENABLED", False):
            return {"enabled": False, "would_penalize": False}
        # Audit placeholder: would check cluster size via scoring._pair_to_cluster at scan layer
        return {"enabled": True, "would_penalize": False, "reason": "scan_layer_only", "would_mult": float(cfg.get("USD_CLUSTER_PENALTY_MULT", 0.85))}
    except Exception as exc:
        return {"enabled": False, "would_penalize": False, "error": str(exc)}


def _diagnose_layer_period_mismatch(profile: Any, indicator_periods: Mapping[str, int]) -> dict:
    """Report-only: does profile trend_layers EMA keys align with resolved periods?"""
    try:
        layers = getattr(profile, "indicator_periods", None) or tuple()
        # indicator_periods came from profile via _resolved_periods / _resolved_indicator_periods_for_tf
        # Mismatch when bond_tlt 34/80 but layers still claim ema21
        period_map = dict(indicator_periods) if isinstance(indicator_periods, dict) else dict(layers)
        trend_period = period_map.get("ema_trend")
        # Layers hardcode ema21/ema50/ema200 names; verify the numeric period behind ema21 matches the group's trend
        # No score change — diagnostic only.
        return {"trend_period": trend_period, "layers": [dict(x) for x in (getattr(profile, "indicator_periods", []) or [])] if hasattr(profile, "indicator_periods") else None}
    except Exception as exc:
        return {"error": str(exc)}


def _v3_session_multiplier(group: str, family: str) -> tuple[float, dict]:
    """Session multiplier for equities/indices (strengthened gate).

    Returns (mult, detail). When ENGINE_A_V3_SESSION_STRENGTHENED disabled, mult=1.0.
    Active = US cash session 13.5-20.0 UTC, else off-hours. Demotes off-hours when enabled.
    """
    try:
        from config import CONFIG
        cfg = CONFIG.get("ENGINE_A_V3_SESSION_STRENGTHENED") or {}
        if not cfg.get("ENABLED", False):
            return 1.0, {"enabled": False}
        if family not in {"equity_etf", "index"} and group not in {"us_indices_trackers", "eu_indices", "asian_indices", "index_other", "us_stock_single", "stock_other", "bond_tlt", "smallcap_em_etf"}:
            return 1.0, {"enabled": True, "applied": False, "reason": "family_not_equity_index"}
        from datetime import datetime, timezone
        hour = datetime.now(timezone.utc).hour + datetime.now(timezone.utc).minute / 60.0
        active = 13.5 <= hour < 20.0
        mult = float(cfg.get("US_ACTIVE_MULT", 1.12) if active else cfg.get("US_OFF_MULT", 0.88))
        return mult, {"enabled": True, "applied": True, "session": "us_active" if active else "off_hours", "utc_hour": round(hour, 2), "mult": mult}
    except Exception as exc:
        return 1.0, {"enabled": False, "error": str(exc)}


def _v3_spread_multiplier(entry_snap: Mapping[str, Any], group: str, asset_type: str) -> tuple[float, dict]:
    """Spread-to-SL penalty (live gate when enabled).

    Uses MAX_EXECUTION_SPREAD_PCT as proxy when snap has no spread_pct.
    Ratio = spread_pct * close / atr  (spread in price / ATR). If ratio > MAX_SPREAD_TO_SL_RATIO, penalize.
    """
    try:
        from config import CONFIG
        cfg = CONFIG.get("ENGINE_A_SPREAD_SCORE_PENALTY") or {}
        if not cfg.get("ENABLED", False):
            return 1.0, {"enabled": False}
        atr = _f(entry_snap.get("atr"))
        close = _f(entry_snap.get("close"))
        if atr is None or atr <= 0 or close is None or close <= 0:
            return 1.0, {"enabled": True, "applied": False, "reason": "missing_atr_close"}
        spread_pct = _f(entry_snap.get("spread_pct")) or _f(entry_snap.get("spreadPct"))
        if spread_pct is None:
            # Fallback to config cap as worst-case proxy
            caps = CONFIG.get("MAX_EXECUTION_SPREAD_PCT") or {}
            # asset_type for equities is stock/index/etf; family equity_etf maps to stock
            key = asset_type if asset_type in caps else ("stock" if asset_type == "stock" else "index" if asset_type == "index" else asset_type)
            spread_pct = _f(caps.get(key)) or _f(caps.get("stock")) or 0.002
        ratio = spread_pct * close / atr
        max_ratio = float(cfg.get("MAX_SPREAD_TO_SL_RATIO", 0.20))
        mult = float(cfg.get("PENALTY_MULT", 0.85))
        if ratio > max_ratio:
            return mult, {"enabled": True, "applied": True, "spreadToAtr": round(ratio, 4), "threshold": max_ratio, "mult": mult}
        return 1.0, {"enabled": True, "applied": False, "spreadToAtr": round(ratio, 4), "threshold": max_ratio, "mult": 1.0}
    except Exception as exc:
        return 1.0, {"enabled": False, "error": str(exc)}


# ── volume / flow ────────────────────────────────────────────────────────────
def _volume_component(
    snap: Mapping[str, Any],
    candles: list[dict],
    context: Mapping[str, Any] | None,
    score_group: str = "",
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

    # Relative volume from the context ratio when a feed supplies one, else
    # derived from candle volume. Only forex populates context["volume_ratio"]
    # (athena.analyze_pair -> duka_volume.get_forex_vr); crypto and stocks always
    # take the derived path, which reads the same Bybit/EODHD volume off the
    # candles. This component sees the entry rung only — there is no D1/H4 volume
    # confirmation in V3, which matters most for crypto (family weight 0.19).
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

    # Tuning Lab optional MFI (Money Flow Index) blend term. Inert unless
    # ENGINE_A_V3_VOLUME_BLEND.MFI_WEIGHT (or a per-group override under
    # ENGINE_A_V3_VOLUME_BLEND.BY_GROUP.<score_group>) is configured > 0
    # (default 0.0) — with no config change signal/quality below are the
    # pre-existing OBV/relative-volume values, unmodified.
    try:
        from config import CONFIG

        volume_blend_cfg = CONFIG.get("ENGINE_A_V3_VOLUME_BLEND") or {}
        mfi_w = _group_scoped_blend_weight(volume_blend_cfg, score_group, "MFI_WEIGHT", 0.0)
    except Exception:
        mfi_w = 0.0
    mfi = _f(snap.get("mfi")) if snap else None
    if mfi_w > 0 and mfi is not None:
        mfi_term = _clamp((mfi - 50.0) / 40.0, -1.0, 1.0)
        mfi_quality = _clamp01(abs(mfi - 50.0) / 40.0)
        w = _clamp01(mfi_w)
        signal = _clamp((1.0 - w) * signal + w * mfi_term, -1.0, 1.0)
        quality = _clamp01((1.0 - w) * quality + w * mfi_quality)

    return Component(signal, _clamp01(quality), available=len(valid) >= 5)


# ── config resolvers ─────────────────────────────────────────────────────────
def _snapshots(
    candles: dict[str, list[dict]], asset_type: str, periods: Mapping[str, int],
    snapshot_cache: dict | None = None,
    *,
    extra_tfs: tuple[str, ...] = (),
    entry_tf: str | None = None,
    entry_periods: Mapping[str, int] | None = None,
    score_group: str | None = None,
) -> dict[str, Mapping[str, Any]]:
    from engine_a_v3.indicator_adapter import indicator_snapshot
    from factor_scoring import (
        entry_tf_uses_period_overrides,
        _resolved_indicator_periods_for_tf,
    )

    snaps: dict[str, Mapping[str, Any]] = {}
    tfs = ("D1", "H4", "H1") + tuple(
        tf for tf in extra_tfs if tf and tf not in ("D1", "H4", "H1")
    )

    # Intraday periods are a property of the timeframe, not of which rung
    # happened to be the entry. Resolving per TF stops a group whose setup rung
    # is H1 from scoring its M30 trigger with H4-scale periods while a group
    # with an M30 setup scores the same timeframe with intraday periods.
    _per_tf_periods: dict[str, Mapping[str, int]] = {}

    def _periods_for(tf: str) -> Mapping[str, int]:
        tf_key = str(tf or "").upper()
        if not entry_tf_uses_period_overrides(tf_key):
            return periods
        if entry_periods and tf_key == str(entry_tf or "").upper():
            return entry_periods
        if score_group is None:
            return entry_periods or periods
        if tf_key not in _per_tf_periods:
            try:
                _per_tf_periods[tf_key] = _resolved_indicator_periods_for_tf(
                    score_group, asset_type, tf_key
                )
            except Exception:
                _per_tf_periods[tf_key] = entry_periods or periods
        return _per_tf_periods[tf_key]

    def _tf_uses_override(tf: str) -> bool:
        return entry_tf_uses_period_overrides(tf)

    for tf in tfs:
        rows = candles.get(tf) or []
        if not rows and tf not in ("D1", "H4", "H1"):
            # An optional rung the caller did not supply (e.g. M5 requested for
            # conditional-M5 refinement while analyze_pair only carries the
            # setup/trigger series). Indicators over zero bars raise IndexError;
            # every read site uses snaps.get(tf) and degrades to unavailable.
            continue
        tf_periods = _periods_for(tf)
        # The shared (tf, len) cache is only keyed by bar count, so it may only
        # hold snapshots built with the group-default periods. Any rung using
        # intraday override periods computes fresh and is never written back.
        cacheable = not _tf_uses_override(tf)
        if snapshot_cache is not None:
            key = (tf, len(rows))
            cached = snapshot_cache.get(key)
            if cached is not None and cacheable:
                snaps[tf] = cached
                continue
            snapshot_at = getattr(snapshot_cache, "snapshot_at", None)
            if callable(snapshot_at):
                cached = snapshot_at(tf, len(rows), tf_periods, asset_type)
                if cached is not None:
                    if cacheable:
                        snapshot_cache[key] = cached
                    snaps[tf] = cached
                    continue
        snap = indicator_snapshot(rows, tf_periods, asset_type)
        if snapshot_cache is not None and cacheable:
            snapshot_cache[(tf, len(rows))] = snap
        snaps[tf] = snap
    return snaps


def _unavailable_quant(
    profile: Any,
    *,
    entry_tf: str | None,
    rejection_reason: str,
) -> QuantScore:
    unavailable = Component(0.0, 0.0, available=False)
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
            "entryTimeframe": entry_tf,
            "rejectionReason": rejection_reason,
        },
        components={
            "trend": unavailable,
            "momentum": unavailable,
            "location": unavailable,
            "volume": unavailable,
        },
    )


# ── public entry point ───────────────────────────────────────────────────────
def score_pair(
    route: Any,
    horizon: str,
    candles: dict[str, list[dict]],
    *,
    context: Mapping[str, Any] | None = None,
    profile: Any | None = None,
    snapshot_cache: dict | None = None,
    current_price: float | None = None,
    entry_tf_override: str | None = None,
    policy_timeframes: Mapping[str, Any] | None = None,
    series_cache=None,
    feature_cache: dict | None = None,
    compact_result: bool = False,
) -> QuantScore:
    """Continuous quality score for one pair. `route` is a SpecialistRoute
    (.score_group, .family). `context` carries subsystem snapshots and an
    optional 'volume_ratio'.

    ``entry_tf_override`` is diagnostic-only (H1/M15/M30). When set, primary
    entry candles/snaps come from that TF with no silent H1/H4 fallback.
    Trend stack remains D1/H4/H1.
    """
    group = getattr(route, "score_group", "unknown")
    family = getattr(route, "family", "unknown")
    asset_type = _FAMILY_ASSET.get(family, "other")
    horizon_raw = str(horizon or "").strip().lower()
    if horizon_raw not in ("intraday", "swing"):
        # Fail closed on unsupported horizons instead of silently coercing to
        # swing: a typo'd horizon would otherwise score with the wrong profile.
        from engine_a_v3.profile import baseline_profile
        return _unavailable_quant(
            profile or baseline_profile(group, "swing"),
            entry_tf=None,
            rejection_reason="unsupported_horizon",
        )
    horizon = horizon_raw
    diagnostic_override = resolve_diagnostic_v3_entry_timeframe(entry_tf_override)
    policy = policy_timeframes if isinstance(policy_timeframes, Mapping) else None
    policy_setup_tf = str((policy or {}).get("setup") or (policy or {}).get("setupTf") or "").upper()
    policy_trigger_tf = str((policy or {}).get("trigger") or (policy or {}).get("triggerTf") or "").upper()
    policy_m5 = str(
        (policy or {}).get("m5_policy") or (policy or {}).get("m5Policy") or ""
    ).lower()
    # Entry confirmation runs on the rung that actually carries authority: the
    # M15 prerequisite when policy marks M5 conditional refinement, else the
    # trigger rung itself. See timeframe_policy.resolve_entry_confirmation_tf.
    from timeframe_policy import resolve_entry_confirmation_tf

    policy_confirmation_tf = resolve_entry_confirmation_tf(policy) or policy_trigger_tf
    if policy and policy_setup_tf:
        # Policy-controlled setup/location and trigger/momentum sources are
        # authoritative.  There is deliberately no H1/H4 fallback below.
        entry_tf = policy_setup_tf
    elif entry_tf_override is not None and diagnostic_override is None:
        entry_tf = None
    elif diagnostic_override is not None:
        entry_tf = diagnostic_override
    else:
        entry_tf = _resolve_v3_entry_tf(group, asset_type, horizon)

    if profile is None:
        from engine_a_v3.profile import baseline_profile
        profile = baseline_profile(group, horizon)
    if entry_tf is None:
        return _unavailable_quant(
            profile, entry_tf=None, rejection_reason="invalid_entry_timeframe"
        )

    entry_candles = candles.get(entry_tf) or []
    if not entry_candles and (diagnostic_override is not None or policy):
        # Fail closed: never fall back to H1/H4 when the entry timeframe was
        # named explicitly. The live evaluator validates the policy setup and
        # trigger rungs before calling here, but direct callers (backtest
        # replay, ablation harnesses) reach this path unvalidated and must not
        # score an empty entry series as a merely low-quality signal.
        return _unavailable_quant(
            profile,
            entry_tf=entry_tf,
            rejection_reason="missing_entry_candles",
        )

    plan_key = (
        "plan",
        group,
        asset_type,
        horizon,
        entry_tf,
        policy_setup_tf,
        policy_trigger_tf,
        policy_confirmation_tf,
        policy_m5,
        getattr(profile, "profile_sha256", None),
    )
    plan = feature_cache.get(plan_key) if feature_cache is not None else None
    if plan is None:
        tf_diagnostics: dict[str, Any] = {}
        if policy:
            if _policy_trend_weights_from_profile_enabled():
                tf_weights, tf_diagnostics = _policy_trend_weights_from_profile(
                    policy, group, asset_type, horizon
                )
            else:
                tf_weights = {}
                for key, alias, weight in _POLICY_ROLE_TF_WEIGHTS:
                    tf = str((policy.get(key) or policy.get(alias) or "")).upper()
                    if tf:
                        tf_weights[tf] = tf_weights.get(tf, 0.0) + weight
                tf_diagnostics = {"trendWeightSource": "policy_role_table_legacy"}
            momentum_tf = _resolve_policy_momentum_tf(
                group, asset_type, horizon, policy_trigger_tf
            )
        else:
            tf_weights = _resolve_v3_tf_weights(group, asset_type, horizon)
            momentum_tf = _resolve_v3_momentum_tf(group, asset_type, horizon)
            tf_diagnostics = {"trendWeightSource": "profile_static"}
        _extra_candidates = {
            *tf_weights.keys(),
            entry_tf,
            momentum_tf,
            policy_trigger_tf,
            policy_confirmation_tf,
        }
        if policy_m5 == "conditional":
            _extra_candidates.add("M5")
        extra_tfs = tuple(
            tf
            for tf in _extra_candidates
            if tf and tf not in ("D1", "H4", "H1")
        )
        plan = (
            tf_weights,
            momentum_tf,
            extra_tfs,
            dict(profile.indicator_periods),
            tf_diagnostics,
        )
        if feature_cache is not None:
            feature_cache[plan_key] = plan
    tf_weights, momentum_tf, extra_tfs, indicator_periods, tf_diagnostics = plan
    from factor_scoring import (
        entry_tf_uses_period_overrides,
        _resolved_indicator_periods_for_tf,
    )

    entry_tf_key = str(entry_tf or "").upper()
    entry_periods: dict[str, int] | None = None
    trend_indicator_periods = indicator_periods
    if entry_tf_uses_period_overrides(entry_tf_key):
        entry_periods = _resolved_indicator_periods_for_tf(
            group, asset_type, entry_tf_key
        )
        trend_indicator_periods = entry_periods
    snap_kwargs = {
        "entry_tf": entry_tf,
        "entry_periods": entry_periods,
        "score_group": group,
    }
    if extra_tfs:
        snaps = _snapshots(
            candles,
            asset_type,
            indicator_periods,
            snapshot_cache,
            extra_tfs=extra_tfs,
            **snap_kwargs,
        )
    else:
        snaps = _snapshots(
            candles,
            asset_type,
            indicator_periods,
            snapshot_cache,
            **snap_kwargs,
        )
    if diagnostic_override is not None or policy:
        entry_snap = snaps.get(entry_tf) or {}
    else:
        entry_snap = snaps.get(entry_tf) or snaps.get("H4") or snaps.get("D1") or {}
    momentum_snap = (
        (snaps.get(momentum_tf) or {})
        if policy else
        (snaps.get(momentum_tf) or snaps.get("H4") or entry_snap)
    )

    trend_key = (
        "trend",
        entry_tf,
        tuple(
            (tf, len(candles.get(tf) or []))
            for tf in sorted(set((*tf_weights.keys(), entry_tf)))
        ),
    )
    trend_result = feature_cache.get(trend_key) if feature_cache is not None else None
    if trend_result is None:
        trend_result = _trend_component(
            snaps,
            tf_weights,
            entry_candles=entry_candles,
            indicator_periods=trend_indicator_periods,
            entry_tf=entry_tf,
            series_cache=series_cache,
        )
        if feature_cache is not None:
            previous_key = feature_cache.get("_latest_trend_key")
            if previous_key is not None and previous_key != trend_key:
                feature_cache.pop(previous_key, None)
            feature_cache[trend_key] = trend_result
            feature_cache["_latest_trend_key"] = trend_key
    trend, coherence = trend_result
    momentum, mom_diag = _momentum_component(momentum_snap, asset_type, group)
    trigger_evidence = None
    if policy_confirmation_tf:
        trigger_snap = snaps.get(policy_confirmation_tf) or {}
        if policy_confirmation_tf == momentum_tf:
            trigger_momentum, trigger_mom_diag = momentum, mom_diag
        else:
            trigger_momentum, trigger_mom_diag = _momentum_component(
                trigger_snap,
                asset_type,
                group,
            )
        trigger_inputs_available = any(
            trigger_mom_diag.get(key) is not None
            for key in ("rsiTerm", "diTerm", "macdSlopeTerm")
        )
        trigger_evidence = {
            "timeframe": policy_confirmation_tf,
            "source": "trigger_timeframe_momentum",
            "available": bool(
                trigger_snap
                and trigger_momentum.available
                and trigger_inputs_available
            ),
            "signal": round(trigger_momentum.signal, 4),
            "quality": round(trigger_momentum.quality, 4),
        }
        if policy_confirmation_tf != policy_trigger_tf:
            # M5 stays refinement: recorded for diagnostics, never the gate.
            trigger_evidence["policyTriggerTf"] = policy_trigger_tf
            trigger_evidence["confirmationRung"] = "execution_prerequisite"
    # Conditional-M5 refinement evidence (fast groups): never votes on direction;
    # only used by evaluator trigger confirmation as pullback turn confirm.
    m5_refinement_evidence = None
    if policy_m5 == "conditional":
        m5_snap = snaps.get("M5") or {}
        if m5_snap:
            m5_mom, m5_diag = _momentum_component(m5_snap, asset_type, group)
            m5_inputs = any(
                m5_diag.get(key) is not None
                for key in ("rsiTerm", "diTerm", "macdSlopeTerm")
            )
            m5_refinement_evidence = {
                "timeframe": "M5",
                "source": "m5_pullback_refinement",
                "available": bool(m5_mom.available and m5_inputs),
                "signal": round(m5_mom.signal, 4),
                "quality": round(m5_mom.quality, 4),
            }
        else:
            m5_refinement_evidence = {
                "timeframe": "M5",
                "source": "m5_pullback_refinement",
                "available": False,
                "signal": 0.0,
                "quality": 0.0,
            }
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
                "entryTimeframe": entry_tf,
                **(
                    {"triggerEvidence": trigger_evidence}
                    if trigger_evidence is not None
                    else {}
                ),
            },
            components={"trend": trend, "momentum": momentum, "location": Component(0.0, 0.0), "volume": Component(0.0, 0.0)},
        )
    # Momentum-anchor ADX corroborates the mean-reversion regime switch, which is
    # otherwise decided entirely on the entry/setup rung.
    corroborating_adx = _f(momentum_snap.get("adx")) if momentum_snap else None
    location_price = (
        _f(current_price) if current_price is not None else _f(entry_snap.get("close"))
    )
    location_price_source = (
        "current_price" if current_price is not None else "confirmed_setup_close"
    )
    core_key = (
        "entry_core",
        entry_tf,
        len(entry_candles),
        (context or {}).get("volume_ratio"),
        corroborating_adx,
        current_price is not None,
        location_price,
    )
    core_result = feature_cache.get(core_key) if feature_cache is not None else None
    if core_result is None:
        location, level_style = _location_component(
            entry_snap,
            asset_type,
            group,
            corroborating_adx=corroborating_adx,
            current_price=current_price,
        )
        volume = _volume_component(entry_snap, entry_candles, context, group)
        core_result = location, level_style, volume
        if feature_cache is not None:
            previous_key = feature_cache.get("_latest_entry_core_key")
            if previous_key is not None and previous_key != core_key:
                feature_cache.pop(previous_key, None)
            feature_cache[core_key] = core_result
            feature_cache["_latest_entry_core_key"] = core_key
    else:
        location, level_style, volume = core_result

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
        # Only AVAILABLE subsystems reserve budget / enter the denom. NA,
        # unavailable, and neutral stubs must not shrink core weights unevenly
        # across pairs with sparse carry/COT coverage.
        sub_budget = sum(
            resolve_subsystem_weights(family).get(name, 0.0)
            for name in SUBSYSTEM_FACTORS
            if subsystem_states.get(name) == ST_AVAILABLE
        )
        sub_budget = min(0.35, sub_budget)
        price_scale = max(0.65, 1.0 - sub_budget) if sub_budget > 0 else 1.0
        for name in CORE_COMPONENTS:
            combined_weights[name] = weights.get(name, 0.0) * price_scale
        for name in SUBSYSTEM_FACTORS:
            if subsystem_states.get(name) == ST_AVAILABLE:
                combined_weights[name] = resolve_subsystem_weights(family).get(name, 0.0)
            else:
                combined_weights[name] = 0.0

    active = {
        name: comp
        for name, comp in components.items()
        if name in CORE_COMPONENTS
        and comp.available
        and (name != "volume" or comp.quality > 0.0 or comp.signal != 0.0)
    }
    # dir_sum normalizer: only components that actually cast a directional vote.
    # A non-directional component (trend-mode location) contributes no term, so
    # leaving its weight in the denominator would silently damp |dir_sum| and
    # push borderline signals under the direction deadband / ramp floor.
    weight_sum = 0.0
    for name, comp in active.items():
        if not comp.directional:
            continue
        weight_sum += max(0.0, combined_weights.get(name, 0.0))
    if subsystems_enabled():
        for name in SUBSYSTEM_FACTORS:
            w = max(0.0, combined_weights.get(name, 0.0))
            if w > 0 and subsystem_states.get(name) != ST_NA:
                weight_sum += w
    weight_sum = weight_sum or 1.0

    def _subsystem_contributes(name: str) -> bool:
        state = subsystem_states.get(name)
        return state == ST_AVAILABLE and max(0.0, combined_weights.get(name, 0.0)) > 0

    # Confluence uses the full configured weight budget so unavailable components
    # cannot inflate scores by shrinking the divisor (L-1).
    #
    # The per-component volatility multiplier is applied to the denominator as
    # well as the numerator, so score_frac stays a true weighted average of
    # component quality. Previously the multiplier scaled only the numerator,
    # which made MAX_SCORE unreachable in low-ADX regimes and saturated (then
    # clamped away) the top of the scale in high-ADX regimes — i.e. the "/3.00"
    # denominator shown on the card was not the achievable maximum. Disable with
    # ENGINE_A_V3_VOLATILITY_GATING.NORMALIZE_DENOMINATOR: false.
    vol_mults = _component_vol_mults(entry_snap, group, asset_type)
    _vol_denom_normalized = _volatility_denominator_normalized()

    def _denom_vol_mult(name: str) -> float:
        if not _vol_denom_normalized or name not in CORE_COMPONENTS:
            return 1.0
        return vol_mults.get(name, 1.0)

    confluence_denom = sum(
        max(0.0, combined_weights.get(name, 0.0)) * _denom_vol_mult(name)
        for name in CORE_COMPONENTS
    )
    # Weight budget that could still be earned: components with no data can
    # never contribute, so the reachable maximum is below MAX_SCORE for them.
    attainable_weight = sum(
        max(0.0, combined_weights.get(name, 0.0)) * _denom_vol_mult(name)
        for name in CORE_COMPONENTS
        if name in active
    )
    if subsystems_enabled():
        for name in SUBSYSTEM_FACTORS:
            if _subsystem_contributes(name):
                confluence_denom += max(0.0, combined_weights.get(name, 0.0))
                attainable_weight += max(0.0, combined_weights.get(name, 0.0))
    confluence_denom = confluence_denom or 1.0

    # Direction = sign of the weighted directional sum over active components,
    # except in mean-reversion regime where the fade direction is authoritative.
    dir_terms: list[tuple[float, float]] = []
    for n, c in active.items():
        if not c.directional:
            continue
        dir_terms.append((combined_weights.get(n, 0.0), c.signal * c.quality))
    if subsystems_enabled():
        for n in SUBSYSTEM_FACTORS:
            if _subsystem_contributes(n):
                comp = components[n]
                dir_terms.append((combined_weights.get(n, 0.0), comp.signal * comp.quality))
    dir_sum = sum(w * term for w, term in dir_terms) / weight_sum
    mr_opposition_blocked = False
    if level_style == "mean_reversion" and location.signal != 0.0:
        direction = "LONG" if location.signal > 0 else "SHORT"
        dsign = 1.0 if location.signal > 0 else -1.0
        # Soft guard: if trend+momentum both oppose the fade with high quality,
        # fall back to consensus direction instead of forcing the fade.
        try:
            from config import CONFIG

            mr_guard = CONFIG.get("ENGINE_A_V3_MR_OPPOSITION_GUARD") or {}
            if mr_guard.get("ENABLED", True):
                min_q = float(mr_guard.get("MIN_OPPOSE_QUALITY", 0.55))
                require_both = bool(mr_guard.get("REQUIRE_BOTH", True))
                loc_sign = 1.0 if location.signal > 0 else -1.0
                trend_opp = (
                    trend.available
                    and trend.quality >= min_q
                    and (trend.signal * loc_sign) < 0.0
                )
                mom_opp = (
                    momentum.available
                    and momentum.quality >= min_q
                    and (momentum.signal * loc_sign) < 0.0
                )
                opposed = (trend_opp and mom_opp) if require_both else (trend_opp or mom_opp)
                if opposed:
                    mr_opposition_blocked = True
                    if dir_sum > profile.direction_deadband:
                        direction, dsign = "LONG", 1.0
                    elif dir_sum < -profile.direction_deadband:
                        direction, dsign = "SHORT", -1.0
                    else:
                        direction, dsign = "FLAT", 0.0
                    level_style = "trend"
        except Exception:
            pass
    elif dir_sum > profile.direction_deadband:
        direction, dsign = "LONG", 1.0
    elif dir_sum < -profile.direction_deadband:
        direction, dsign = "SHORT", -1.0
    else:
        direction, dsign = "FLAT", 0.0

    # Directional ramp (V2 port): abort weak |dir_sum|; soft-span multiplies confluence.
    dir_ramp_mult = 1.0
    min_directional_failed = False
    try:
        from config import CONFIG

        ramp_cfg = CONFIG.get("ENGINE_A_V3_DIRECTIONAL_RAMP") or {}
        if ramp_cfg.get("ENABLED", True) and direction in ("LONG", "SHORT"):
            from engine_a_v3.directional_ramp import resolve_v3_directional_ramp

            min_dir, soft_span = resolve_v3_directional_ramp(asset_type, group)
            abs_dir = abs(dir_sum)
            if abs_dir < min_dir:
                min_directional_failed = True
                direction, dsign = "FLAT", 0.0
                dir_ramp_mult = 0.0
            elif soft_span > 0 and abs_dir < min_dir + soft_span:
                dir_ramp_mult = (abs_dir - min_dir) / soft_span
            else:
                dir_ramp_mult = 1.0
            if not ramp_cfg.get("APPLY_TO_CONFLUENCE", True):
                dir_ramp_mult = 1.0 if not min_directional_failed else 0.0
    except Exception:
        dir_ramp_mult = round(0.5 + 0.5 * abs(dir_sum), 4)

    # Confluence = weighted aligned quality. Directional components that disagree
    # with the chosen direction contribute nothing (continuous), but never veto.
    # Non-directional components (trend-mode location) are credited on quality
    # alone once a direction exists — they measure entry timing, not side.
    # Dedicated Trend/Momentum boxes + the rest as "orthogonal" factor boxes,
    # matching the cockpit card. Each box shows signed strength (signal*quality).
    factor_scores: dict[str, Any] = {}
    ortho: dict[str, float] = {}
    score_frac = 0.0
    for name, comp in components.items():
        weight = max(0.0, combined_weights.get(name, 0.0))
        in_direction_pool = name in active or (subsystems_enabled() and _subsystem_contributes(name))
        aligned = bool(dsign) and (
            not comp.directional or (comp.signal * dsign) > 0.0
        )
        vol_mult = vol_mults.get(name, 1.0) if name in CORE_COMPONENTS else 1.0
        if in_direction_pool and aligned:
            score_frac += weight * comp.quality * vol_mult
        if not compact_result:
            signed = round(comp.signal * comp.quality, 4)
            if name in ("trend", "momentum"):
                factor_scores[name] = signed
            elif name in CORE_COMPONENTS:
                ortho[name] = signed
            else:
                ortho[name] = signed
    score_frac /= confluence_denom

    vol_mult = _volatility_mult(entry_snap) if not compact_result else 1.0
    confluence = _clamp(MAX_SCORE * score_frac * max(0.0, dir_ramp_mult), 0.0, MAX_SCORE)
    threshold = profile.trade_threshold
    # Ceiling this pair can actually reach with the data it has. A missing
    # component (e.g. no usable volume feed) keeps its weight in the divisor by
    # design, so the reachable maximum drops below MAX_SCORE and the group
    # threshold can become unreachable with no signal-level trace. Reported so
    # the funnel can distinguish "scored low" from "could not score high".
    max_attainable = round(MAX_SCORE * (attainable_weight / confluence_denom), 4)
    unavailable_components = sorted(
        name
        for name in CORE_COMPONENTS
        if name not in active and max(0.0, combined_weights.get(name, 0.0)) > 0
    )
    threshold_unreachable = bool(threshold > 0 and max_attainable + 1e-9 < threshold)
    decision = "TRADE" if direction in ("LONG", "SHORT") and confluence >= threshold else "WATCH"

    from engine_a_v3.legacy_filters import apply_legacy_filters

    confluence, decision, legacy_diag = apply_legacy_filters(
        asset_type=asset_type,
        score_group=group,
        family=family,
        direction=direction,
        confluence=confluence,
        decision=decision,
        snaps=snaps,
        candles=candles,
        coherence=coherence,
        mom_diag=mom_diag,
        entry_tf=entry_tf,
        series_cache=series_cache,
        level_style=level_style,
        policy=policy,
    )
    confluence = _clamp(float(confluence), 0.0, MAX_SCORE)
    # After multiplier, re-tier if score fell below threshold (never upgrade).
    if decision == "TRADE" and confluence < threshold:
        decision = "WATCH"
        legacy_diag = dict(legacy_diag)
        legacy_diag["legacyMultDemotedTrade"] = True

    equity_volume_blocked = False
    if family == "equity_etf" and decision == "TRADE":
        try:
            from config import CONFIG

            vol_cfg = CONFIG.get("ENGINE_A_V3_EQUITY_VOLUME_FLOOR") or {}
            if vol_cfg.get("ENABLED", True):
                min_q = float(vol_cfg.get("MIN_QUALITY", 0.15))
                if (not volume.available) or volume.quality < min_q:
                    decision = "WATCH"
                    equity_volume_blocked = True
        except Exception:
            decision = "WATCH"
            equity_volume_blocked = True

    # ── Session / Spread gates (enabled 2026-08-06 with approval) ──────
    # These demote TRADE→WATCH when enabled; previous report-only path is now live.
    session_mult, session_gate_detail = _v3_session_multiplier(group, family)
    spread_mult, spread_gate_detail = _v3_spread_multiplier(entry_snap, group, asset_type)
    pre_session_spread_confluence = confluence
    gate_mult = session_mult * spread_mult
    session_spread_demotion = False
    if gate_mult != 1.0:
        confluence = _clamp(confluence * gate_mult, 0.0, MAX_SCORE)
        if decision == "TRADE" and confluence < threshold:
            decision = "WATCH"
            session_spread_demotion = True

    crypto_deriv_blocked = False
    if family == "crypto" and decision == "TRADE":
        try:
            from config import CONFIG

            deriv_cfg = CONFIG.get("ENGINE_A_V3_CRYPTO_DERIV_GUARD") or {}
            if deriv_cfg.get("ENABLED", False):
                ctx = context or {}
                conflict = bool(ctx.get("funding_conflict") or ctx.get("oi_conflict"))
                fresh = ctx.get("derivatives_fresh")
                require_fresh = bool(deriv_cfg.get("REQUIRE_FRESH", True))
                if conflict:
                    decision = "WATCH"
                    crypto_deriv_blocked = True
                elif require_fresh and fresh is not True:
                    # Fail closed when guard is on but freshness unknown/stale.
                    decision = "WATCH"
                    crypto_deriv_blocked = True
        except Exception:
            decision = "WATCH"
            crypto_deriv_blocked = True

    score_norm = confluence / MAX_SCORE

    # Diagnostics (group-vol is now live via _component_vol_mults; spread/session are live gates above)
    try:
        _group_vol_diag = _report_only_group_vol_bands_diagnostic(entry_snap, group, asset_type)
        _spread_diag = _report_only_spread_diagnostic(entry_snap, group)
        _corr_diag = _report_only_correlation_diagnostic(group, family)
        _layer_diag = _diagnose_layer_period_mismatch(profile, indicator_periods)
    except Exception:
        _group_vol_diag = {"enabled": False}
        _spread_diag = {"enabled": False}
        _corr_diag = {"enabled": False}
        _layer_diag = {}

    if compact_result:
        factor_diagnostics = {
            "atrPct": _f(entry_snap.get("atr_pct")),
            "locationPrice": location_price,
            "locationPriceSource": location_price_source,
            "minDirectionalFailed": min_directional_failed,
            "legacyFilters": legacy_diag,
            "equityVolumeBlocked": equity_volume_blocked,
            "cryptoDerivBlocked": crypto_deriv_blocked,
            "maxAttainableScore": max_attainable,
            "thresholdUnreachable": threshold_unreachable,
            "sessionGate": session_gate_detail,
            "spreadGate": spread_gate_detail,
            "sessionSpreadGateMult": round(gate_mult, 4),
            "sessionSpreadDemoted": session_spread_demotion,
            "preGatesConfluence": round(pre_session_spread_confluence, 4),
            "reportOnly": {
                "groupVolBands": _group_vol_diag,
                "spread": _spread_diag,
                "correlation": _corr_diag,
                "layerPeriodMismatch": _layer_diag,
            },
            **(
                {"triggerEvidence": trigger_evidence}
                if trigger_evidence is not None
                else {}
            ),
        }
    else:
        factor_scores["ortho"] = ortho
        factor_scores["ortho_term"] = round(sum(ortho.values()), 4)
        factor_diagnostics = {
            "entryTimeframe": entry_tf,
            "entryTfOverride": diagnostic_override,
            "locationPrice": location_price,
            "locationPriceSource": location_price_source,
            "scoringTimeframes": {
                "policyApplied": bool(policy),
                "trend": list(tf_weights.keys()),
                "trendWeights": {tf: round(w, 4) for tf, w in tf_weights.items()},
                "momentum": momentum_tf,
                "location": entry_tf,
                "volume": entry_tf,
                "setup": policy_setup_tf or entry_tf,
                "trigger": policy_trigger_tf or momentum_tf,
                "m5Policy": policy_m5 or "disabled",
                # ADX reaches the score from two snapshots: the momentum anchor
                # (gate + multiplier) and the entry rung (trend-health slope /
                # plateau). Both are reported so a divergence is visible; the
                # thresholds themselves remain asset-class keyed, not TF-aware.
                "momentumAdx": momentum_tf,
                "trendHealthAdx": entry_tf,
                **tf_diagnostics,
            },
            "m5Policy": policy_m5 or "disabled",
            "adxValue": mom_diag.get("adxValue"),
            "adxMultiplier": mom_diag.get("adxMultiplier"),
            "diAlignMult": mom_diag.get("diAlignMult"),
            "dirSum": round(dir_sum, 4),
            "directionalRampMult": round(dir_ramp_mult, 4),
            "minDirectionalFailed": min_directional_failed,
            "mrOppositionBlocked": mr_opposition_blocked,
            "mrAdxCeiling": round(_resolve_mr_adx_ceiling(asset_type, group), 4) if level_style == "mean_reversion" else None,
            "trendCoherence": coherence or {"error": "no_ema_data"},
            # Reachable ceiling given this pair's available components (see
            # max_attainable above). thresholdUnreachable=true means the group
            # threshold cannot be met with the data present, regardless of setup
            # quality — a data problem, not a weak signal.
            "maxAttainableScore": max_attainable,
            "unavailableComponents": unavailable_components or None,
            "thresholdUnreachable": threshold_unreachable,
            "volatilityDenominatorNormalized": _vol_denom_normalized,
            "volatilityMult": round(vol_mult, 4),
            "componentVolMults": {k: round(v, 4) for k, v in vol_mults.items()},
            "atrPct": _f(entry_snap.get("atr_pct")),
            "subsystemsEnabled": subsystems_enabled(),
            "subsystemStates": subsystem_states or None,
            "legacyFilters": legacy_diag,
            "trendState": legacy_diag.get("trendState"),
            "equityVolumeBlocked": equity_volume_blocked,
            "cryptoDerivBlocked": crypto_deriv_blocked,
            "sessionGate": session_gate_detail,
            "spreadGate": spread_gate_detail,
            "sessionSpreadGateMult": round(gate_mult, 4),
            "sessionSpreadDemoted": session_spread_demotion,
            "preGatesConfluence": round(pre_session_spread_confluence, 4),
            "reportOnly": {
                "groupVolBands": _group_vol_diag,
                "spread": _spread_diag,
                "correlation": _corr_diag,
                "layerPeriodMismatch": _layer_diag,
            },
            **(
                {"triggerEvidence": trigger_evidence}
                if trigger_evidence is not None
                else {}
            ),
            **(
                {"m5RefinementEvidence": m5_refinement_evidence}
                if m5_refinement_evidence is not None
                else {}
            ),
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
            "entryBarCount": len(entry_candles),
            "entryLastClose": (
                float(entry_candles[-1]["close"])
                if entry_candles and isinstance(entry_candles[-1], dict)
                and entry_candles[-1].get("close") is not None
                else None
            ),
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
