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
import math

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
    subsystem_weight_scope,
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
    collapsed = max(0, len(role_map) - len(tf_weights))
    expanded = False
    # When regime/bias/structure collide (swing D1×3 or bias==structure),
    # redistribute profile weights onto distinct setup/trigger rungs so the
    # triple-screen stack is not pure single-TF EMA.
    if collapsed > 0 and ordered_weights:
        setup_tf = str(
            policy.get("setup") or policy.get("setupTf") or ""
        ).upper()
        trigger_tf = str(
            policy.get("trigger") or policy.get("triggerTf") or ""
        ).upper()
        slots: list[str] = []
        for key in ("regime", "bias", "structure"):
            tf = role_map.get(key)
            if tf and tf not in slots:
                slots.append(tf)
        for tf in (setup_tf, trigger_tf):
            if tf and tf not in slots:
                slots.append(tf)
        if len(slots) >= 2 and len(slots) > len(tf_weights):
            redistributed: dict[str, float] = {}
            n = min(len(slots), len(ordered_weights))
            for i in range(n):
                redistributed[slots[i]] = float(ordered_weights[i])
            if len(ordered_weights) > n:
                redistributed[slots[-1]] = redistributed.get(slots[-1], 0.0) + sum(
                    float(w) for w in ordered_weights[n:]
                )
            total = sum(redistributed.values())
            if total > 0:
                tf_weights = redistributed
                expanded = True
    diagnostics = {
        "trendWeightSource": (
            "policy_roles_style_fallback"
            if layer_count_mismatch
            else (
                "policy_roles_expanded_setup_trigger"
                if expanded
                else "policy_roles_profile_weights"
            )
        ),
        "trendRoleTimeframes": dict(role_map),
        # Profile weights are calibrated by timeframe name but applied by role
        # position, so a policy promotion redirects a calibrated weight onto a
        # different timeframe. Emit the resolved (role, tf, weight) triple.
        "trendRoleWeights": dict(role_weights),
        "trendLayersCollapsed": collapsed,
        "trendLayersExpanded": expanded,
        "trendLayerCountMismatch": layer_count_mismatch,
    }
    return tf_weights, diagnostics


def _resolve_v3_tf_weights(score_group: str, asset_type: str, horizon: str) -> dict[str, float]:
    """Resolve per-group TF weights for the V3 trend component.

    Wires V3 to ``ENGINE_A_SCORING_PROFILE`` so per-group TF overrides take live
    effect (e.g., energy_oil/commodity_other use 0.20/0.45/0.35 intraday;
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
    if not math.isfinite(out):
        return default
    return out


def _clamp(value: float, lo: float, hi: float) -> float:
    return lo if value < lo else hi if value > hi else value


def _clamp01(value: float) -> float:
    return _clamp(value, 0.0, 1.0)


def _parse_bar_time(value: Any):
    """Parse a candle timestamp to an aware UTC datetime, or None.

    Local to this module so the session gate does not import the evaluator
    (which imports this module) and stays usable from the ablation/backtest
    harnesses that never load the evaluator.
    """
    from datetime import datetime, timezone

    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


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
def _tf_trend(
    snap: Mapping[str, Any], *, structure_only: bool = False
) -> tuple[float, str, bool]:
    """One timeframe's EMA-stack vote -> (-1..1, label, available).

    ``structure_only`` drops the close-vs-trend-EMA vote and keeps the EMA
    ordering + long-EMA slope votes. Used for the setup/trigger rungs: their
    close-vs-EMA read is the pullback the location component already scores,
    and voting it here made the trend component weakest at exactly the
    retracement the engine is built to buy (review 2026-09-02, finding 3).
    """
    close = _f(snap.get("close"))
    e_trend = _f(snap.get("ema21"))   # trend EMA (group period)
    e_mom = _f(snap.get("ema50"))     # momentum EMA
    e_long = _f(snap.get("ema200"))   # long EMA
    if close is None or e_trend is None or e_mom is None or e_long is None:
        return 0.0, "FLAT", False
    votes = [
        1.0 if e_trend > e_mom else -1.0,
        1.0 if e_mom > e_long else -1.0,
    ]
    if not structure_only:
        votes.insert(0, 1.0 if close > e_trend else -1.0)
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


_TF_LADDER_ORDER: dict[str, int] = {
    "D1": 0, "H4": 1, "H1": 2, "M30": 3, "M15": 4, "M5": 5, "M1": 6,
}


def _trend_structure_only_tfs(
    tf_weights: Mapping[str, float], setup_tf: str | None
) -> frozenset[str]:
    """Trend-stack rungs that vote on EMA structure only (no close-vs-EMA).

    Every rung at or below the setup rung: for the universal intraday ladder
    that is H1; for swing (setup H4, trigger H1) it is H4 and H1; for the
    equity intraday ladder (structure H1, setup M30) no stack rung qualifies,
    so the H1 structure rung keeps its full vote. Reversible via
    ``ENGINE_A_V3_TREND_STACK.SETUP_RUNG_STRUCTURE_VOTE_ONLY: false``.
    """
    try:
        from config import CONFIG

        cfg = CONFIG.get("ENGINE_A_V3_TREND_STACK") or {}
        if not bool(cfg.get("SETUP_RUNG_STRUCTURE_VOTE_ONLY", True)):
            return frozenset()
    except Exception:
        pass
    setup_index = _TF_LADDER_ORDER.get(str(setup_tf or "").upper())
    if setup_index is None:
        return frozenset()
    return frozenset(
        str(tf).upper()
        for tf in tf_weights
        if _TF_LADDER_ORDER.get(str(tf).upper(), -1) >= setup_index
    )


def _trend_health_adx_source() -> str:
    try:
        from config import CONFIG

        cfg = CONFIG.get("ENGINE_A_V3_TREND_HEALTH") or {}
        value = str(cfg.get("ADX_SLOPE_SOURCE", "momentum_anchor") or "momentum_anchor")
    except Exception:
        value = "momentum_anchor"
    value = value.strip().lower()
    return value if value in {"entry", "momentum_anchor", "off"} else "momentum_anchor"


def _tf_stack_separation(snap: Mapping[str, Any]) -> float | None:
    """Tightest gap in the EMA stack, in ATR units (None when unavailable).

    ``_tf_trend`` votes are binary, so a stack separated by 0.02 ATR scores
    identically to one separated by 3 ATR — the component carried direction but
    no magnitude at all. The *minimum* pairwise gap is used rather than the mean
    because a stack is only as clean as its tightest rung: one compressed gap is
    the EMA-cluster condition, and averaging hides it behind a wide
    ema50/ema200 spread.
    """
    close = _f(snap.get("close"))
    e_trend = _f(snap.get("ema21"))
    e_mom = _f(snap.get("ema50"))
    e_long = _f(snap.get("ema200"))
    atr = _f(snap.get("atr"))
    if None in (close, e_trend, e_mom, e_long) or atr is None or atr <= 0:
        return None
    gaps = (
        abs(close - e_trend),      # type: ignore[operator]
        abs(e_trend - e_mom),      # type: ignore[operator]
        abs(e_mom - e_long),       # type: ignore[operator]
    )
    return min(gaps) / atr


def _trend_separation_params() -> tuple[bool, float, float]:
    enabled, target, floor = True, 0.25, 0.65
    try:
        from config import CONFIG

        cfg = CONFIG.get("ENGINE_A_V3_TREND_SEPARATION") or {}
        enabled = bool(cfg.get("ENABLED", True))
        target = float(cfg.get("TARGET_ATR", 0.25))
        floor = float(cfg.get("FLOOR", 0.65))
    except Exception:
        pass
    if target <= 0:
        target = 0.25
    return enabled, target, _clamp01(floor)


def _trend_component(
    snaps: dict[str, Mapping[str, Any]],
    tf_w: Mapping[str, float],
    *,
    entry_candles: list[dict] | None = None,
    indicator_periods: Mapping[str, int] | None = None,
    entry_tf: str = "H1",
    series_cache=None,
    structure_only_tfs: frozenset[str] | None = None,
    health_adx_tf: str | None = None,
) -> tuple[Component, dict[str, Any]]:
    parts: dict[str, float] = {}
    coherence: dict[str, Any] = {}
    weighted = 0.0
    total_w = 0.0
    sep_weighted = 0.0
    sep_w_total = 0.0
    _structure_only = structure_only_tfs or frozenset()
    for tf, w in tf_w.items():
        snap = snaps.get(tf)
        if not snap:
            continue
        score, label, available = _tf_trend(
            snap, structure_only=str(tf).upper() in _structure_only
        )
        if not available:
            continue
        parts[tf] = score
        coherence[tf.lower()] = label
        weighted += w * score
        total_w += w
        sep = _tf_stack_separation(snap)
        if sep is not None:
            sep_weighted += w * sep
            sep_w_total += w
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
    configured_timeframes = [str(tf).upper() for tf in tf_w]
    available_timeframes = [str(tf).upper() for tf in parts]
    dominant_label = "UP" if weighted > 0.0 else "DOWN" if weighted < 0.0 else "MIXED"
    agreement_count = sum(
        1 for label in coherence.values() if label == dominant_label
    )
    total_count = len(parts)
    coherence.update(
        {
            # Direct lower-case TF keys above remain for backward compatibility.
            # These explicit collections make the contract dynamic and let
            # consumers ignore non-TF metadata safely.
            "per_tf": {
                str(tf).upper(): {
                    "direction": coherence.get(str(tf).lower()),
                    "signal": round(parts[tf], 4),
                    "weight": round(float(tf_w.get(tf, 0.0)), 4),
                }
                for tf in parts
            },
            "trend_timeframes": configured_timeframes,
            "available_timeframes": available_timeframes,
            "configured_count": len(configured_timeframes),
            "total_count": total_count,
            "agreement_count": agreement_count,
            "coherence_ratio": round(
                agreement_count / total_count if total_count else 0.0, 4
            ),
            "tf_coverage": round(
                total_count / len(configured_timeframes)
                if configured_timeframes
                else 0.0,
                4,
            ),
            "dominant_direction": (
                "LONG" if dominant_label == "UP" else
                "SHORT" if dominant_label == "DOWN" else
                None
            ),
            "weighted_balance": round(weighted, 4),
        }
    )
    quality = abs(weighted) * (0.5 + 0.5 * coherence_q)
    # Separation scales quality, never direction: a compressed stack is a
    # low-conviction read of the same side, not the other side.
    sep_enabled, sep_target, sep_floor = _trend_separation_params()
    if sep_enabled and sep_w_total > 0:
        sep_mean = sep_weighted / sep_w_total
        sep_mult = sep_floor + (1.0 - sep_floor) * _clamp01(sep_mean / sep_target)
        # Separation stays a quality-only multiplier. Coherence consumers that
        # need TF labels read the explicit per_tf map or filter the compatible
        # lower-case TF keys above.
        quality *= _clamp(sep_mult, sep_floor, 1.0)
    quality *= _trend_health_mult(
        weighted, snaps, entry_candles, indicator_periods, entry_tf=entry_tf,
        series_cache=series_cache, adx_tf=health_adx_tf,
    )
    if _structure_only:
        coherence["structure_only_timeframes"] = sorted(_structure_only)
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
    adx_tf: str | None = None,
) -> float:
    """Penalize stale or weakening trends (config-gated, default on).

    ``adx_tf`` is the momentum-anchor rung. With ``ADX_SLOPE_SOURCE:
    momentum_anchor`` (default) the ADX-slope and plateau reads come from that
    snapshot instead of the entry rung: a five-bar ADX10 slope on H1 penalised
    68% of EUR/USD bars (mean 0.906, 12% at the floor) on a D1/H4-led trend —
    that is entry-rung noise, not trend health (review 2026-09-02, finding 4).
    ``entry`` restores the previous read; ``off`` disables the ADX branches and
    keeps only the alignment-age penalty.
    """
    enabled = True
    start_bars = 8
    bar_penalty = 0.03
    floor = 0.75
    adx_weakening_penalty = 0.15
    plateau_enabled = False
    plateau_mult = 0.85
    adx_source = "momentum_anchor"
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
        adx_source = str(cfg.get("ADX_SLOPE_SOURCE", "momentum_anchor") or "momentum_anchor")
    except Exception:
        pass
    adx_source = adx_source.strip().lower()
    if adx_source not in {"entry", "momentum_anchor", "off"}:
        adx_source = "momentum_anchor"
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
    _adx_key = str(adx_tf or "").upper()
    if adx_source == "momentum_anchor" and _adx_key and snaps.get(_adx_key):
        adx_snap = snaps[_adx_key]
    else:
        adx_snap = entry_snap
    # ADX is unsigned trend strength: a falling slope means the trend is
    # weakening regardless of direction, so the penalty applies symmetrically
    # to LONG and SHORT (previously the SHORT branch penalized rising ADX —
    # i.e. strengthening downtrends — inverting the intent for shorts).
    adx_slope = _f(adx_snap.get("adxSlope"), 0.0) or 0.0
    if adx_source != "off" and adx_slope < 0:
        mult *= _clamp(1.0 + adx_weakening_penalty * adx_slope, floor, 1.0)

    # Plateau penalty is off by default: a steady ADX above 25 is a stable
    # trend, not a stale one — actual weakening is the negative-slope branch
    # above. Re-enable via ENGINE_A_V3_TREND_HEALTH.PLATEAU_PENALTY_ENABLED.
    adx = _f(adx_snap.get("adx"), 0.0) or 0.0
    adx_prev = _f(adx_snap.get("adxPrev"))
    if (
        adx_source != "off"
        and plateau_enabled
        and adx_prev is not None
        and adx > 25
        and abs(adx - adx_prev) < 1.0
    ):
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


def _momentum_aligned_quality_only() -> bool:
    """Whether momentum confidence counts only sub-terms on the blended side.

    Quality was the weighted mean of every term's magnitude regardless of sign,
    so a DI reading that opposed the blended RSI/MACD direction *added*
    confidence: RSI 62 / DI +0.30 / MACD rising scored signal 0.495, quality
    0.495; flipping DI to -0.30 dropped the signal to 0.285 and left quality at
    0.495. Because the aggregator credits quality (not signal x quality) for any
    aligned component, conflicted momentum was paid in full (review
    2026-09-02, finding 1). Default true: opposing terms keep their weight in
    the divisor and contribute nothing to the numerator — the same rule the
    top-level confluence loop applies across components. Reversible via
    ``ENGINE_A_V3_MOMENTUM_BLEND.ALIGNED_QUALITY_ONLY: false``.
    """
    try:
        from config import CONFIG

        cfg = CONFIG.get("ENGINE_A_V3_MOMENTUM_BLEND") or {}
        return bool(cfg.get("ALIGNED_QUALITY_ONLY", True))
    except Exception:
        return True


def _rsi_exhaustion_params() -> tuple[bool, float, float]:
    """(enabled, exhaustion_start_rsi, floor_multiplier) for RSI quality taper."""
    enabled, start, floor = True, 75.0, 0.45
    try:
        from config import CONFIG

        cfg = CONFIG.get("ENGINE_A_V3_MOMENTUM_BLEND") or {}
        enabled = bool(cfg.get("RSI_EXHAUSTION_ENABLED", True))
        start = float(cfg.get("RSI_EXHAUSTION_START", 75.0))
        floor = float(cfg.get("RSI_EXHAUSTION_FLOOR", 0.45))
    except Exception:
        pass
    start = _clamp(start, 51.0, 99.0)
    return enabled, start, _clamp01(floor)


def _macd_quality_magnitude_params() -> tuple[bool, float]:
    """(enabled, atr_scale) for the MACD histogram-magnitude quality factor."""
    enabled, scale = True, 0.35
    try:
        from config import CONFIG

        cfg = CONFIG.get("ENGINE_A_V3_MOMENTUM_BLEND") or {}
        enabled = bool(cfg.get("MACD_QUALITY_MAGNITUDE_ENABLED", True))
        scale = float(cfg.get("MACD_QUALITY_ATR_SCALE", 0.35))
    except Exception:
        pass
    if scale <= 0:
        scale = 0.35
    return enabled, scale


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
    # Keep confidence on the same declared indicator-weight basis as direction.
    # A zero-weight term must be absent from both halves of the component; the
    # previous unweighted mean let zero-weight RSI/DI/MACD readings change the
    # headline score and gave any positive ROC weight an equal quality vote.
    # Entries: (weight, quality, signed term, name). The sign lets the aligned-
    # only quality rule below drop terms that oppose the blended direction.
    quality_terms: list[tuple[float, float, float, str]] = []

    rsi = _f(snap.get("rsi"))
    if rsi is not None:
        rsi_term = _clamp((rsi - 50.0) / 30.0, -1.0, 1.0)
        # Quality (confidence in the reading), not the directional term, is
        # tapered past the exhaustion bound. The linear |rsi-50|/30 curve peaked
        # at RSI 80/20 — it scored a fully extended, late-stage move as the
        # single most confident momentum reading available, with no term
        # anywhere in the component representing reversal risk. The signal keeps
        # its sign and magnitude; only the confidence attached to it decays.
        rsi_dev = abs(rsi - 50.0)
        rsi_quality = _clamp01(rsi_dev / 30.0)
        exhaustion_enabled, exhaustion_start, exhaustion_floor = _rsi_exhaustion_params()
        if exhaustion_enabled:
            start_dev = max(1.0, float(exhaustion_start) - 50.0)
            if rsi_dev > start_dev:
                span = max(1e-9, 50.0 - start_dev)
                excess = _clamp01((rsi_dev - start_dev) / span)
                rsi_quality *= 1.0 - (1.0 - exhaustion_floor) * excess
                diag["rsiExhaustionExcess"] = round(excess, 4)
        weighted_signal += rsi_w * rsi_term
        weight_total += rsi_w
        if rsi_w > 0:
            quality_terms.append((rsi_w, _clamp01(rsi_quality), rsi_term, "rsi"))
        diag["rsiTerm"] = round(rsi_term, 4)
        diag["rsiQuality"] = round(_clamp01(rsi_quality), 4)

    di_p = _f(snap.get("plusDI"))
    di_m = _f(snap.get("minusDI"))
    if di_p is not None and di_m is not None and (di_p + di_m) > 0:
        di_term = _clamp((di_p - di_m) / (di_p + di_m), -1.0, 1.0)
        weighted_signal += di_w * di_term
        weight_total += di_w
        if di_w > 0:
            quality_terms.append((di_w, abs(di_term), di_term, "di"))
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
        # |macd_term| alone floors at 0.48 for ANY non-zero histogram (the
        # +/-0.8 sign term x the 0.6 weakening factor), so a histogram of 1e-6
        # contributed the same confidence as a full-scale one and put a hard
        # floor under momentum quality. Scale the confidence by the histogram's
        # magnitude in ATR units; the directional term is untouched.
        macd_quality = _clamp01(abs(macd_term))
        mag_enabled, mag_scale = _macd_quality_magnitude_params()
        macd_atr = _f(snap.get("atr"))
        if mag_enabled and macd_atr is not None and macd_atr > 0 and mag_scale > 0:
            magnitude = _clamp01(abs(hist) / (macd_atr * mag_scale))
            macd_quality = _clamp01(macd_quality * magnitude)
            diag["macdMagnitude"] = round(magnitude, 4)
        if macd_w > 0:
            quality_terms.append((macd_w, macd_quality, macd_term, "macd"))
        diag["macdSlopeTerm"] = round(macd_term, 4)
        diag["macdQuality"] = round(macd_quality, 4)

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
            quality_terms.append((roc_w, _clamp01(abs(roc) / roc_scale), roc_term, "roc"))
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
    quality_weight_total = sum(weight for weight, _q, _t, _n in quality_terms)
    aligned_only = _momentum_aligned_quality_only()
    signal_sign = 1.0 if signal > 0 else -1.0 if signal < 0 else 0.0
    opposed_terms: list[str] = []
    credited = 0.0
    for weight, term_quality, term_value, term_name in quality_terms:
        term_sign = 1.0 if term_value > 0 else -1.0 if term_value < 0 else 0.0
        aligned = term_sign == 0.0 or (signal_sign != 0.0 and term_sign == signal_sign)
        if aligned or not aligned_only:
            credited += weight * term_quality
        if not aligned and term_sign != 0.0:
            opposed_terms.append(term_name)
    base_quality = credited / quality_weight_total if quality_weight_total > 0 else 0.0
    diag["qualityWeightTotal"] = round(quality_weight_total, 4)
    diag["alignedQualityOnly"] = aligned_only
    if opposed_terms:
        diag["opposedTerms"] = opposed_terms
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


def _location_trend_quality_scaling_params() -> tuple[bool, float]:
    """(enabled, floor) for scaling trend-mode location credit by trend quality."""
    enabled, floor = True, 0.4
    try:
        from config import CONFIG

        cfg = (CONFIG.get("ENGINE_A_V3_LOCATION") or {}).get("TREND_QUALITY_SCALING") or {}
        if isinstance(cfg, Mapping):
            enabled = bool(cfg.get("ENABLED", True))
            floor = float(cfg.get("FLOOR", 0.4))
    except Exception:
        pass
    return enabled, _clamp01(floor)


def _location_direction_aware_params() -> tuple[bool, float, float, float, float]:
    """(enabled, pullback_free, pullback_decay, chase_free, chase_decay) in ATR."""
    enabled = True
    pullback_free, pullback_decay = 1.0, 2.5
    chase_free, chase_decay = 0.25, 1.75
    try:
        from config import CONFIG

        cfg = CONFIG.get("ENGINE_A_V3_LOCATION") or {}
        enabled = bool(cfg.get("DIRECTION_AWARE_TIMING", True))
        pullback_free = float(cfg.get("PULLBACK_FREE_ATR", pullback_free))
        pullback_decay = float(cfg.get("PULLBACK_DECAY_ATR", pullback_decay))
        chase_free = float(cfg.get("CHASE_FREE_ATR", chase_free))
        chase_decay = float(cfg.get("CHASE_DECAY_ATR", chase_decay))
    except Exception:
        pass
    if pullback_decay <= 0:
        pullback_decay = 2.5
    if chase_decay <= 0:
        chase_decay = 1.75
    return enabled, max(0.0, pullback_free), pullback_decay, max(0.0, chase_free), chase_decay


def _location_timing_quality(dist: float, dsign: float) -> float:
    """Two-sided entry-timing quality for trend-mode location.

    ``dist`` is (price - trend EMA) / ATR; ``dsign`` is +1 LONG / -1 SHORT.

    In timing-only mode the component is non-directional, so the aggregator
    credits it on quality alone whenever a direction exists. With a single
    symmetric curve that made it a near-constant: any pair sitting within half
    an ATR of its trend EMA earned the full weight for LONG and for SHORT
    alike — up to 0.66 of the 3.0 scale on forex_majors, awarded most reliably
    in chop, where price sits on the EMA. A term identical on both sides adds no
    directional information and just compresses the discriminating range.

    The curve is now asymmetric about the trade direction: a retracement toward
    the EMA (the entry the component was written to reward) holds full quality
    further, while an equal extension *in* the trade direction — chasing — decays
    faster. dist == 0 scores 1.0 on both sides, so the neutral point is unchanged.
    """
    _, pullback_free, pullback_decay, chase_free, chase_decay = _location_direction_aware_params()
    extension = abs(dist)
    if dist * dsign <= 0.0:  # retracement side (or exactly at the EMA)
        free, decay = pullback_free, pullback_decay
    else:                     # extended in the trade direction
        free, decay = chase_free, chase_decay
    return _clamp01(1.0 - max(0.0, extension - free) / decay)


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


def _mean_reversion_fails_closed_on_missing_adx() -> bool:
    """Missing ADX must not read as 'maximally ranging' for the MR flip.

    The regime switch previously passed both of its ADX tests on absent data:
    a missing entry-rung ADX coerced to 0.0 (always below the ceiling) and a
    missing momentum-anchor ADX defaulted the higher-TF check to True, so a
    stretched candle with no ADX history flipped straight into a counter-trend
    fade. Fail closed instead: no ADX on a required test disqualifies the
    mean-reversion branch (trend timing is unaffected). Reversible:
    ``ENGINE_A_V3_MEAN_REVERSION.FAIL_CLOSED_ON_MISSING_ADX: false``.
    """
    try:
        from config import CONFIG

        cfg = CONFIG.get("ENGINE_A_V3_MEAN_REVERSION") or {}
        return bool(cfg.get("FAIL_CLOSED_ON_MISSING_ADX", True))
    except Exception:
        return True


def _finite_adx(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:  # NaN
        return None
    return number


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
    mr_adx_fail_closed = _mean_reversion_fails_closed_on_missing_adx()

    entry_adx = _finite_adx(snap.get("adx"))
    higher_tf_ranging = True
    if _mean_reversion_requires_higher_tf_confirmation():
        anchor_adx = _finite_adx(corroborating_adx)
        if anchor_adx is not None:
            higher_tf_ranging = anchor_adx < mr_adx_ceiling
        elif mr_adx_fail_closed:
            higher_tf_ranging = False

    entry_adx_usable = entry_adx is not None or not mr_adx_fail_closed
    if (
        entry_adx_usable
        and adx < mr_adx_ceiling
        and stretched
        and higher_tf_ranging
    ):
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


def _report_only_spread_diagnostic(
    entry_snap: Mapping[str, Any], group: str, asset_type: str = ""
) -> dict:
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
        spread_pct = _f(entry_snap.get("spread_pct"))
        if spread_pct is None:
            spread_pct = _f(entry_snap.get("spreadPct"))
        if spread_pct is None or spread_pct <= 0:
            return {"enabled": True, "would_penalize": False, "reason": "no_spread_on_snap"}
        close = _f(entry_snap.get("close")) or 1.0
        # Same SL basis as the live gate so the two can never disagree.
        from engine_a_v3.levels import resolve_structural_geometry

        sl_atr_mult = float(
            resolve_structural_geometry(asset_type).get("sl_min_atr_mult", 0.8) or 0.8
        )
        sl_distance = atr * sl_atr_mult
        ratio = spread_pct * close / sl_distance if sl_distance > 0 else 0.0
        max_ratio = float(cfg.get("MAX_SPREAD_TO_SL_RATIO", 0.20))
        mult = float(cfg.get("PENALTY_MULT", 0.85))
        would = ratio > max_ratio
        return {"enabled": True, "would_penalize": would, "spreadToSl": round(ratio, 4), "threshold": max_ratio, "would_mult": mult if would else 1.0}
    except Exception as exc:
        return {"enabled": False, "would_penalize": False, "error": str(exc)}


def _resolve_correlation_cluster_size(context: Mapping[str, Any] | None) -> tuple[int | None, str]:
    """Read the concurrent same-cluster signal count from scan context.

    Cluster membership is a portfolio-level fact (how many *other* signals in
    this scan share the pair's dominant risk factor), so it cannot be derived
    inside a single-pair scorer. The scan supplies it as
    ``context["correlation_cluster"] = {"size": N, "label": "USD"}`` — or the
    flat ``context["correlation_cluster_size"]``. Absent context yields
    ``(None, reason)`` and the gate stays inert rather than guessing.
    """
    ctx = context or {}
    cluster = ctx.get("correlation_cluster")
    label = ""
    raw: Any = None
    if isinstance(cluster, Mapping):
        raw = cluster.get("size")
        label = str(cluster.get("label") or "")
    if raw is None:
        raw = ctx.get("correlation_cluster_size")
    if raw is None:
        return None, "cluster_context_not_supplied"
    try:
        size = int(raw)
    except (TypeError, ValueError):
        return None, "cluster_context_invalid"
    if size < 0:
        return None, "cluster_context_invalid"
    return size, label or "cluster"


def _v3_correlation_multiplier(
    context: Mapping[str, Any] | None, group: str, family: str
) -> tuple[float, dict]:
    """Correlation-cluster penalty (live gate when enabled and fed).

    Previously a hardcoded placeholder that returned ``would_penalize: False``
    unconditionally, so the config knob had no implementation behind it. The
    penalty now applies whenever the scan supplies a cluster size above
    ``MAX_CLUSTER_SIZE``; without that context it reports exactly why it is
    inert instead of implying a check ran.
    """
    try:
        from config import CONFIG

        cfg = CONFIG.get("ENGINE_A_CORRELATION_SCORE_PENALTY") or {}
        if not cfg.get("ENABLED", False):
            return 1.0, {"enabled": False}
        size, label = _resolve_correlation_cluster_size(context)
        max_size = int(cfg.get("MAX_CLUSTER_SIZE", 2) or 2)
        mult = float(cfg.get("USD_CLUSTER_PENALTY_MULT", 0.85))
        if size is None:
            return 1.0, {
                "enabled": True,
                "applied": False,
                "reason": label,
                "maxClusterSize": max_size,
                "mult": 1.0,
            }
        detail = {
            "enabled": True,
            "clusterLabel": label,
            "clusterSize": size,
            "maxClusterSize": max_size,
            "family": family,
            "group": group,
        }
        if size > max_size:
            return mult, {**detail, "applied": True, "mult": mult}
        return 1.0, {**detail, "applied": False, "mult": 1.0}
    except Exception as exc:
        return 1.0, {"enabled": False, "error": str(exc)}


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


# Cash-session resolution is calendar-based (DST-aware): each group/symbol is
# scored against the exchange its constituents actually trade on, and the cash
# window is derived from that exchange's local session hours on the bar's own
# date via exchange_calendars (IANA timezones). The previous fixed UTC windows
# were summer-time values: November-March the US cash session is 14:30-21:00
# UTC, so the old (13.5, 20.0) window promoted the hour before the open and
# demoted the final hour of trading (audit A-1).
#
# Fixed UTC windows are retained ONLY for groups whose members span several
# exchanges with no single representative calendar (asian_indices covers HKEX,
# Nikkei and ASX; the union window is fixed by construction).
#
# Groups whose members span several exchanges without a representative venue
# (stock_other holds the ATFX US / Europe / Hong Kong share CFDs; index_other
# is unclassified) are deliberately absent: without a per-symbol region there
# is no correct window, and guessing one is worse than not applying the gate.
# `context["symbol"]` resolves those per pair when a caller supplies it (see
# _resolve_session_calendar).
_SESSION_WINDOWS_UTC: dict[str, tuple[float, float]] = {
    "asian_indices": (0.0, 8.0),
}

# score_group -> exchange_calendars key whose continuous session is the group's
# representative cash window. eu_indices spans several venues (DAX, FTSE,
# CAC/MIB/IBEX); XETRA hours are the representative choice — an approximation
# disclosed rather than a per-symbol split the group key cannot carry.
_GROUP_SESSION_CALENDAR: dict[str, str] = {
    "us_indices_trackers": "NYSE_NASDAQ",
    "us_stock_single": "NYSE_NASDAQ",
    "bond_tlt": "NYSE_NASDAQ",
    "smallcap_em_etf": "NYSE_NASDAQ",
    "eu_indices": "XETRA",
}


def _resolve_session_calendar(group: str, symbol: str | None) -> tuple[str | None, str]:
    """Resolve an exchange_calendars key for a group / optional symbol."""
    calendar = _GROUP_SESSION_CALENDAR.get(group)
    if calendar is not None:
        return calendar, group
    raw = str(symbol or "").strip()
    if not raw:
        return None, "region_unresolved"
    try:
        from engine_a_groups import resolve_cash_equity_exchange_calendar

        calendar = resolve_cash_equity_exchange_calendar(raw)
    except Exception:
        return None, "region_unresolved"
    if not calendar:
        return None, "region_unresolved"
    return calendar, calendar


def _v3_session_multiplier(
    group: str,
    family: str,
    *,
    bar_time: Any = None,
    symbol: str | None = None,
) -> tuple[float, dict]:
    """Session multiplier for equities/indices (strengthened gate).

    Returns (mult, detail). When ENGINE_A_V3_SESSION_STRENGTHENED disabled, mult=1.0.

    ``bar_time`` is the entry candle's timestamp. The multiplier must be a
    property of the bar being scored, not of when the scan happens to run:
    reading ``datetime.now()`` made a replayed/backtested bar take whatever
    session was current at replay time, so live and backtest could never agree
    on the same bar. Missing/unparseable bar time fails open (mult 1.0) rather
    than substituting wall clock.

    Session membership is resolved through exchange_calendars on the bar's own
    date, so DST transitions shift the window with the exchange (audit A-1).
    When the timezone database is unavailable the gate fails open, loudly.
    """
    try:
        from config import CONFIG
        from engine_a_v3.gate_toggles import session_gates_enabled

        cfg = CONFIG.get("ENGINE_A_V3_SESSION_STRENGTHENED") or {}
        if not session_gates_enabled(CONFIG) or not cfg.get("ENABLED", False):
            return 1.0, {
                "enabled": False,
                "reason": (
                    "session_gates_disabled"
                    if not session_gates_enabled(CONFIG)
                    else "session_strengthened_disabled"
                ),
            }
        if family not in {"equity_etf", "index"} and group not in {"us_indices_trackers", "eu_indices", "asian_indices", "index_other", "us_stock_single", "stock_other", "bond_tlt", "smallcap_em_etf"}:
            return 1.0, {"enabled": True, "applied": False, "reason": "family_not_equity_index"}
        parsed = _parse_bar_time(bar_time)
        if parsed is None:
            return 1.0, {
                "enabled": True,
                "applied": False,
                "reason": "bar_time_unavailable",
            }
        calendar, calendar_source = _resolve_session_calendar(group, symbol)
        if calendar:
            try:
                from exchange_calendars import SessionPhase, compute_session_phase

                phase = compute_session_phase(calendar, parsed)
            except Exception:
                phase = None
            if phase is None:
                return 1.0, {
                    "enabled": True,
                    "applied": False,
                    "reason": "session_calendar_unavailable",
                    "calendar": calendar,
                    "windowSource": calendar_source,
                }
            active = phase == SessionPhase.OPEN
            mult = float(cfg.get("US_ACTIVE_MULT", 1.12) if active else cfg.get("US_OFF_MULT", 0.88))
            return mult, {
                "enabled": True,
                "applied": True,
                "session": "cash_active" if active else "off_hours",
                "calendar": calendar,
                "sessionPhase": str(getattr(phase, "value", phase)),
                "windowSource": calendar_source,
                "barUtcHour": round(parsed.hour + parsed.minute / 60.0, 2),
                "timeSource": "entry_candle",
                "mult": mult,
            }
        window = _SESSION_WINDOWS_UTC.get(group)
        if window is None:
            return 1.0, {
                "enabled": True,
                "applied": False,
                "reason": "session_window_unresolved_for_group",
                "group": group,
            }
        hour = parsed.hour + parsed.minute / 60.0
        start, end = window
        active = start <= hour < end
        mult = float(cfg.get("US_ACTIVE_MULT", 1.12) if active else cfg.get("US_OFF_MULT", 0.88))
        return mult, {
            "enabled": True,
            "applied": True,
            "session": "cash_active" if active else "off_hours",
            "windowSource": calendar_source,
            "windowUtc": [start, end],
            "barUtcHour": round(hour, 2),
            "timeSource": "entry_candle",
            "mult": mult,
        }
    except Exception as exc:
        return 1.0, {"enabled": False, "error": str(exc)}


def _v3_spread_multiplier(entry_snap: Mapping[str, Any], group: str, asset_type: str) -> tuple[float, dict]:
    """Spread-to-SL penalty (live gate when enabled).

    Requires a *live* spread on the snapshot. The previous implementation fell
    back to the ``MAX_EXECUTION_SPREAD_PCT`` cap whenever ``spread_pct`` was
    absent — and it is always absent, because no producer writes a spread onto
    an indicator snapshot (see indicator_adapter._snapshot_from_bundle and
    indicators.calc_indicators' snap keys). With a constant substituted for the
    numerator the test collapsed to ``atr/close < 5 x cap``, i.e. a pure
    low-volatility penalty applied on every scan: the forex trip point (0.0025)
    is the ceiling of the whole forex ATR%% band in VOLATILITY_SCALER_BANDS,
    so every forex pair took the penalty while crypto (trip point 0.010 = its
    band floor) almost never did. That is a cross-asset score distortion, not a
    spread gate, so a missing spread now fails open and says so.

    The denominator is the stop distance, matching the "spread-to-SL" name.
    Levels are built after scoring, so the SL is approximated by the same
    ``SL_MIN_ATR_MULT`` the geometry resolver uses for the structural floor;
    the basis is reported so the approximation is visible.
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
        spread_pct = _f(entry_snap.get("spread_pct"))
        if spread_pct is None:
            spread_pct = _f(entry_snap.get("spreadPct"))
        if spread_pct is None or spread_pct <= 0:
            # Fail open, loudly. Substituting the configured cap here turned the
            # gate into a blanket volatility haircut (see docstring).
            return 1.0, {
                "enabled": True,
                "applied": False,
                "reason": "live_spread_unavailable",
                "mult": 1.0,
            }
        from engine_a_v3.levels import resolve_structural_geometry

        sl_atr_mult = float(resolve_structural_geometry(asset_type).get("sl_min_atr_mult", 0.8) or 0.8)
        sl_distance = atr * sl_atr_mult
        if sl_distance <= 0:
            return 1.0, {"enabled": True, "applied": False, "reason": "invalid_sl_basis"}
        ratio = spread_pct * close / sl_distance
        max_ratio = float(cfg.get("MAX_SPREAD_TO_SL_RATIO", 0.20))
        mult = float(cfg.get("PENALTY_MULT", 0.85))
        detail = {
            "enabled": True,
            "spreadToSl": round(ratio, 4),
            "threshold": max_ratio,
            "slBasis": "atr_x_sl_min_atr_mult",
            "slAtrMult": round(sl_atr_mult, 4),
        }
        if ratio > max_ratio:
            return mult, {**detail, "applied": True, "mult": mult}
        return 1.0, {**detail, "applied": False, "mult": 1.0}
    except Exception as exc:
        return 1.0, {"enabled": False, "error": str(exc)}


# ── volume / flow ────────────────────────────────────────────────────────────
_CRYPTO_EXCHANGE_VOLUME_SOURCES = frozenset({"bybit", "binance_futures"})
_EQUITY_EXCHANGE_VOLUME_SOURCES = frozenset({"eodhd"})
_FOREX_CONTEXT_VOLUME_SOURCES = frozenset({"dukascopy"})
# Checked-in live overlay policy admits only pair type ``stock``. ETF, index,
# and commodity groups must remain unavailable even if a delayed/research row
# happens to carry an EODHD label.
_EODHD_LIVE_STOCK_SCORE_GROUPS = frozenset(
    {"us_stock_single", "stock_other"}
)


def _normalize_volume_source(raw: Any) -> str | None:
    source = str(raw or "").strip().lower()
    aliases = {
        "bybit_linear_kline": "bybit",
        "binance": "binance_futures",
        "binance_usdm": "binance_futures",
        "duka": "dukascopy",
        "dukascopy_ecn": "dukascopy",
        "eodhd_ws": "eodhd",
        "eodhd_live_v2": "eodhd",
    }
    source = aliases.get(source, source)
    return source or None


def _canonical_volume_source(candle: Mapping[str, Any]) -> str | None:
    raw = candle.get("volSource") or candle.get("volumeSource")
    if not raw:
        raw = candle.get("provider")
    return _normalize_volume_source(raw)


def _allowed_volume_sources(score_group: str) -> frozenset[str]:
    group = str(score_group or "").strip().lower()
    if group.startswith("crypto_"):
        return _CRYPTO_EXCHANGE_VOLUME_SOURCES
    if group in _EODHD_LIVE_STOCK_SCORE_GROUPS:
        return _EQUITY_EXCHANGE_VOLUME_SOURCES
    return frozenset()


def _allowed_context_volume_sources(score_group: str) -> frozenset[str]:
    group = str(score_group or "").strip().lower()
    if group.startswith("forex_"):
        return _FOREX_CONTEXT_VOLUME_SOURCES
    return frozenset()


def _volume_provenance_diagnostic(
    candles: list[dict],
    score_group: str,
    *,
    required: bool,
    context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    window = [
        candle
        for candle in candles[-20:]
        if isinstance(candle, Mapping) and (_f(candle.get("vol")) or 0.0) > 0.0
    ]
    source_counts: dict[str, int] = {}
    for candle in window:
        source = _canonical_volume_source(candle) or "missing"
        source_counts[source] = source_counts.get(source, 0) + 1
    allowed = _allowed_volume_sources(score_group)
    detail: dict[str, Any] = {
        "required": bool(required),
        "scoreGroup": str(score_group or ""),
        "allowedSources": sorted(allowed),
        "sourceCounts": source_counts,
        "barsWithPositiveVolume": len(window),
    }
    disallowed: list[str] = []
    if not window:
        candle_accepted = False
        candle_reason = "no_positive_volume"
    elif not required:
        candle_accepted = True
        candle_reason = (
            "legacy_unstamped_research_series"
            if set(source_counts) == {"missing"}
            else "research_provenance_not_enforced"
        )
    elif not allowed:
        candle_accepted = False
        candle_reason = "score_group_has_no_approved_live_volume_source"
    elif "missing" in source_counts:
        candle_accepted = False
        candle_reason = "volume_source_missing"
    else:
        disallowed = sorted(set(source_counts) - set(allowed))
        if disallowed:
            candle_accepted = False
            candle_reason = "volume_source_not_approved"
        elif len(source_counts) != 1:
            candle_accepted = False
            candle_reason = "mixed_volume_sources"
        else:
            candle_accepted = True
            candle_reason = "approved_exchange_volume"

    ratio = _f((context or {}).get("volume_ratio"))
    context_source = _normalize_volume_source(
        (context or {}).get("volume_ratio_source")
    )
    allowed_context = _allowed_context_volume_sources(score_group)
    if ratio is None:
        context_accepted = False
        context_reason = "no_context_volume_ratio"
    elif not required:
        context_accepted = True
        context_reason = (
            "legacy_unstamped_context_volume_ratio"
            if context_source is None
            else "research_context_provenance_not_enforced"
        )
    elif context_source is None:
        context_accepted = False
        context_reason = "context_volume_source_missing"
    elif context_source not in allowed_context:
        context_accepted = False
        context_reason = "context_volume_source_not_approved"
    else:
        context_accepted = True
        context_reason = "approved_context_volume_ratio"

    accepted = candle_accepted or context_accepted
    if candle_accepted and context_accepted:
        reason = "approved_candle_and_context_volume"
        selected_inputs = ["candles", "context_ratio"]
    elif candle_accepted:
        reason = candle_reason
        selected_inputs = ["candles"]
    elif context_accepted:
        reason = context_reason
        selected_inputs = ["context_ratio"]
    else:
        reason = candle_reason
        selected_inputs = []
    result = {
        **detail,
        "accepted": accepted,
        "reason": reason,
        "candleAccepted": candle_accepted,
        "candleReason": candle_reason,
        "allowedContextSources": sorted(allowed_context),
        "contextSource": context_source,
        "contextAccepted": context_accepted,
        "contextReason": context_reason,
        "selectedInputs": selected_inputs,
    }
    if disallowed:
        result["disallowedSources"] = disallowed
    return result


def _volume_graded_obv_signal() -> bool:
    try:
        from config import CONFIG

        cfg = CONFIG.get("ENGINE_A_V3_VOLUME_BLEND") or {}
        return bool(cfg.get("GRADED_OBV_SIGNAL", True))
    except Exception:
        return True


def _volume_component(
    snap: Mapping[str, Any],
    candles: list[dict],
    context: Mapping[str, Any] | None,
    score_group: str = "",
    *,
    provenance: Mapping[str, Any] | None = None,
) -> Component:
    provenance_detail = dict(provenance or {}) or _volume_provenance_diagnostic(
        candles,
        score_group,
        required=bool((context or {}).get("volume_provenance_required")),
        context=context,
    )
    if not provenance_detail.get("accepted"):
        return Component(0.0, 0.0, available=False)
    candle_volume_accepted = bool(provenance_detail.get("candleAccepted"))
    context_volume_accepted = bool(provenance_detail.get("contextAccepted"))
    signal = 0.0
    quality = 0.0
    price_rows = [
        c for c in candles[-20:] if _f(c.get("close")) is not None
    ]
    valid = (
        [c for c in price_rows if (_f(c.get("vol")) or 0) > 0]
        if candle_volume_accepted
        else []
    )
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
        flow_total = max(1.0, sum(float(c.get("vol") or 0) for c in valid[midpoint:]))
        imbalance = _clamp(delta / flow_total, -1.0, 1.0)
        # Graded flow vote (default): the signal is the signed net-flow share,
        # not its sign. The bare sign was +/-1 on 88% of BTC/USDT H1 bars at a
        # mean quality of 0.35 — a second full-magnitude short-horizon price
        # vote collinear with trend/momentum (review 2026-09-02, finding 6).
        # ENGINE_A_V3_VOLUME_BLEND.GRADED_OBV_SIGNAL: false restores the sign.
        if _volume_graded_obv_signal():
            signal = imbalance
        else:
            signal = 1.0 if delta > 0 else -1.0 if delta < 0 else 0.0
        quality = min(1.0, abs(imbalance))

    # Relative volume from the context ratio when a feed supplies one, else
    # derived from candle volume. Only forex populates context["volume_ratio"]
    # (athena.analyze_pair -> duka_volume.get_forex_vr); crypto and stocks always
    # take the derived path, which reads the same Bybit/EODHD volume off the
    # candles. This component sees the entry rung only — there is no D1/H4 volume
    # confirmation in V3, which matters most for crypto (family weight 0.19).
    vr = (
        _f((context or {}).get("volume_ratio"))
        if context_volume_accepted
        else None
    )
    if vr is None and candle_volume_accepted and candles:
        vols = [_f(c.get("vol")) for c in candles[-21:]]
        vols = [v for v in vols if v]
        if len(vols) >= 5:
            avg = sum(vols[:-1]) / max(1, len(vols) - 1)
            if avg > 0:
                vr = vols[-1] / avg
    if vr is not None:
        quality = max(quality, _clamp01((vr - 1.0) / 1.5))
        if vr > 1.0 and len(price_rows) >= 2:
            last_close = float(price_rows[-1]["close"])
            prev_close = float(price_rows[-2]["close"])
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
    if candle_volume_accepted and mfi_w > 0 and mfi is not None:
        mfi_term = _clamp((mfi - 50.0) / 40.0, -1.0, 1.0)
        mfi_quality = _clamp01(abs(mfi - 50.0) / 40.0)
        w = _clamp01(mfi_w)
        signal = _clamp((1.0 - w) * signal + w * mfi_term, -1.0, 1.0)
        quality = _clamp01((1.0 - w) * quality + w * mfi_quality)

    return Component(
        signal,
        _clamp01(quality),
        available=(len(valid) >= 5 or (context_volume_accepted and vr is not None)),
    )


def _subsystem_max_directional_share() -> float | None:
    """Cap on the combined subsystem share of the direction vote (None = no cap)."""
    try:
        from config import CONFIG

        cfg = CONFIG.get("ENGINE_A_V3_SUBSYSTEMS") or {}
        raw = cfg.get("MAX_DIRECTIONAL_SHARE", 0.10)
    except Exception:
        raw = 0.10
    if raw is None:
        return None
    try:
        share = float(raw)
    except (TypeError, ValueError):
        return 0.10
    if not math.isfinite(share) or share < 0.0:
        return None
    return min(share, 1.0)


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
    entry_candle_is_forming: bool = False,
) -> QuantScore:
    """Continuous quality score for one pair. `route` is a SpecialistRoute
    (.score_group, .family). `context` carries subsystem snapshots and an
    optional 'volume_ratio'.

    ``entry_tf_override`` is diagnostic-only (H1/M15/M30). When set, primary
    entry candles/snaps come from that TF with no silent H1/H4 fallback.
    When policy provenance is supplied, the trend stack follows its resolved
    regime/bias/structure roles and collapses duplicate timeframes.
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
        tuple(
            str((policy or {}).get(role) or (policy or {}).get(f"{role}Tf") or "").upper()
            for role in ("regime", "bias", "structure")
        ),
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
    # No cross-timeframe fallback on any path: a missing/empty entry snapshot
    # must degrade the entry-rung reads to unavailable, never silently re-anchor
    # them on H4/D1 while volume/alignment-age keep reading the entry series —
    # the mixed-rung read carried no diagnostic (audit A-2). The policy path was
    # already fail-closed; the legacy path now matches it.
    entry_snap = snaps.get(entry_tf) or {}
    momentum_snap = (
        (snaps.get(momentum_tf) or {})
        if policy else
        (snaps.get(momentum_tf) or snaps.get("H4") or entry_snap)
    )

    structure_only_tfs = _trend_structure_only_tfs(
        tf_weights, policy_setup_tf or entry_tf
    )
    _health_adx_source = _trend_health_adx_source()
    health_adx_tf = (
        momentum_tf
        if _health_adx_source == "momentum_anchor" and snaps.get(momentum_tf)
        else entry_tf
    )
    trend_key = (
        "trend",
        entry_tf,
        tuple(
            (
                tf,
                round(float(tf_weights.get(tf, 0.0)), 8),
                len(candles.get(tf) or []),
            )
            for tf in sorted(tf_weights)
        ),
        len(candles.get(entry_tf) or []),
        tuple(sorted(structure_only_tfs)),
        health_adx_tf,
        _health_adx_source,
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
            structure_only_tfs=structure_only_tfs,
            health_adx_tf=health_adx_tf,
        )
        if feature_cache is not None:
            previous_key = feature_cache.get("_latest_trend_key")
            if previous_key is not None and previous_key != trend_key:
                feature_cache.pop(previous_key, None)
            feature_cache[trend_key] = trend_result
            feature_cache["_latest_trend_key"] = trend_key
    trend, coherence = trend_result
    coherence = dict(coherence or {})
    _trend_role_timeframes = tf_diagnostics.get("trendRoleTimeframes")
    if isinstance(_trend_role_timeframes, Mapping):
        coherence["role_timeframes"] = dict(_trend_role_timeframes)
    _trend_role_weights = tf_diagnostics.get("trendRoleWeights")
    if isinstance(_trend_role_weights, Mapping):
        coherence["role_weights"] = dict(_trend_role_weights)
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
    volume_candles = (
        entry_candles[:-1]
        if entry_candle_is_forming and len(entry_candles) > 1
        else entry_candles
    )
    volume_provenance = _volume_provenance_diagnostic(
        volume_candles,
        group,
        required=bool((context or {}).get("volume_provenance_required")),
        context=context,
    )
    core_key = (
        "entry_core",
        entry_tf,
        len(entry_candles),
        bool(entry_candle_is_forming),
        (context or {}).get("volume_ratio"),
        (context or {}).get("volume_ratio_source"),
        corroborating_adx,
        current_price is not None,
        location_price,
        bool(volume_provenance.get("accepted")),
        volume_provenance.get("reason"),
        tuple(sorted((volume_provenance.get("sourceCounts") or {}).items())),
        tuple(volume_provenance.get("selectedInputs") or ()),
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
        # Volume must be measured on confirmed bars only. When the entry candle
        # is still forming, its last bar carries a partial volume that
        # understates the derived relative-volume ratio and skews OBV — live
        # would then systematically disagree with the confirmed-bar backtest
        # (audit A-6). Drop the forming bar for the volume read; location keeps
        # the live quote via current_price.
        volume = _volume_component(
            entry_snap,
            volume_candles,
            context,
            group,
            provenance=volume_provenance,
        )
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
    sub_weights: dict[str, float] = {}
    sub_budget = 0.0
    price_scale = 1.0
    sub_weight_scope = "disabled"
    if subsystems_enabled():
        sub_weights = resolve_subsystem_weights(family, group)
        sub_weight_scope = subsystem_weight_scope(family, group)
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
            sub_weights.get(name, 0.0)
            for name in SUBSYSTEM_FACTORS
            if subsystem_states.get(name) == ST_AVAILABLE
        )
        sub_budget = min(0.35, sub_budget)
        price_scale = max(0.65, 1.0 - sub_budget) if sub_budget > 0 else 1.0
        for name in CORE_COMPONENTS:
            combined_weights[name] = weights.get(name, 0.0) * price_scale
        for name in SUBSYSTEM_FACTORS:
            if subsystem_states.get(name) == ST_AVAILABLE:
                combined_weights[name] = sub_weights.get(name, 0.0)
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
    core_dir_weight = 0.0
    for name, comp in active.items():
        if not comp.directional:
            continue
        core_dir_weight += max(0.0, combined_weights.get(name, 0.0))
    weight_sum = core_dir_weight
    # Subsystem direction-share cap. Carry + COT held 26.6% (forex) / 30%
    # (commodity) of the directional weight and could zero a moderate price
    # trend by themselves (AUD/USD at carry +2 sigma resolved FLAT on a
    # moderate downtrend; review 2026-09-02, finding 2). Their vote in dirSum
    # is scaled so the combined subsystem share stays at or under
    # MAX_DIRECTIONAL_SHARE; their confluence credit and the core budget scale
    # are unchanged, so an aligned carry still adds score.
    sub_dir_raw = 0.0
    sub_dir_scale = 1.0
    sub_max_share = _subsystem_max_directional_share()
    if subsystems_enabled():
        for name in SUBSYSTEM_FACTORS:
            w = max(0.0, combined_weights.get(name, 0.0))
            if w > 0 and subsystem_states.get(name) != ST_NA:
                sub_dir_raw += w
        if (
            sub_dir_raw > 0
            and core_dir_weight > 0
            and sub_max_share is not None
            and 0.0 <= sub_max_share < 1.0
        ):
            sub_dir_cap = sub_max_share * core_dir_weight / (1.0 - sub_max_share)
            if sub_dir_raw > sub_dir_cap:
                sub_dir_scale = sub_dir_cap / sub_dir_raw
        weight_sum += sub_dir_raw * sub_dir_scale
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
    # Named, weight-applied contributions to dir_sum, for diagnostics only. A
    # score of 0.00 with a strong coherent trend is otherwise unexplainable from
    # the payload: subsystem drag (carry/sentiment) can cancel trend+momentum
    # and push |dir_sum| under the deadband, which zeroes every contribution.
    dir_contributions: dict[str, float] = {}
    for n, c in active.items():
        if not c.directional:
            continue
        dir_terms.append((combined_weights.get(n, 0.0), c.signal * c.quality))
        dir_contributions[n] = combined_weights.get(n, 0.0) * c.signal * c.quality
    if subsystems_enabled():
        for n in SUBSYSTEM_FACTORS:
            if _subsystem_contributes(n):
                comp = components[n]
                w_dir = combined_weights.get(n, 0.0) * sub_dir_scale
                dir_terms.append((w_dir, comp.signal * comp.quality))
                dir_contributions[n] = w_dir * comp.signal * comp.quality
    dir_sum = sum(w * term for w, term in dir_terms) / weight_sum
    if weight_sum > 0:
        dir_contributions = {
            name: round(value / weight_sum, 6)
            for name, value in dir_contributions.items()
        }
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
            # Fail closed: if the guard configuration cannot be read, do not
            # force the counter-trend fade — fall back to the consensus
            # direction exactly like an opposed fade (audit A-5).
            mr_opposition_blocked = True
            if dir_sum > profile.direction_deadband:
                direction, dsign = "LONG", 1.0
            elif dir_sum < -profile.direction_deadband:
                direction, dsign = "SHORT", -1.0
            else:
                direction, dsign = "FLAT", 0.0
            level_style = "trend"
    elif dir_sum > profile.direction_deadband:
        direction, dsign = "LONG", 1.0
    elif dir_sum < -profile.direction_deadband:
        direction, dsign = "SHORT", -1.0
    else:
        direction, dsign = "FLAT", 0.0

    # A FLAT resolved by the deadband zeroes every component contribution, so
    # the payload shows score 0.00 with no gate or reason attached to it.
    # `minDirectionalFailed` does NOT cover this case: the directional ramp
    # below only runs when direction is already LONG/SHORT, so a sub-deadband
    # dir_sum leaves that flag False. Record it explicitly.
    direction_deadband_flat = direction == "FLAT" and not mr_opposition_blocked

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
        # Fail closed: the ramp is the weak-direction abort gate, so an
        # unreadable ramp configuration must not score the direction through
        # a fail-open soft multiplier (audit A-5). Abort like a sub-minimum
        # dir_sum instead.
        min_directional_failed = True
        direction, dsign = "FLAT", 0.0
        dir_ramp_mult = 0.0

    # ── Direction-aware location timing ──────────────────────────────────────
    # Location is non-directional in timing-only mode, so it is credited on
    # quality alone once a direction exists. Re-score that quality against the
    # chosen side now that the side is known (see _location_timing_quality).
    # Runs after the directional ramp so a ramp-induced FLAT leaves it untouched,
    # and only in trend mode — the mean-reversion branch is genuinely directional
    # and already votes.
    location_timing_diag: dict[str, Any] | None = None
    if (
        level_style != "mean_reversion"
        and dsign
        and not components["location"].directional
        and _location_direction_aware_params()[0]
    ):
        _loc_ema = _f(entry_snap.get("ema21"))
        _loc_atr = _f(entry_snap.get("atr"))
        if (
            location_price is not None
            and _loc_ema is not None
            and _loc_atr is not None
            and _loc_atr > 0
        ):
            _loc_dist = (location_price - _loc_ema) / _loc_atr
            _loc_quality = _location_timing_quality(_loc_dist, dsign)
            location_timing_diag = {
                "distAtr": round(_loc_dist, 4),
                "side": "pullback" if _loc_dist * dsign <= 0 else "extension",
                "qualityBefore": round(components["location"].quality, 4),
                "qualityAfter": round(_loc_quality, 4),
            }
            location = Component(
                components["location"].signal,
                _loc_quality,
                available=components["location"].available,
                directional=False,
            )
            components["location"] = location
            if "location" in active:
                active["location"] = location

    # ── Location credit scaled by trend quality ──────────────────────────────
    # Trend-mode location is non-directional and credited on quality alone, so
    # "price at the EMA in chop" earned the same 0.66 of the 3.0 scale as a
    # pullback inside a real trend (review 2026-09-02, finding 5). Timing is
    # only worth its weight when there is a trend to pull back into: scale the
    # credit by trend quality with a floor so a genuinely aligned entry in a
    # weak stack is discounted, not erased. Mean-reversion location is
    # directional and votes; it is left alone.
    location_trend_scale_diag: dict[str, Any] | None = None
    if (
        level_style != "mean_reversion"
        and dsign
        and not components["location"].directional
        and components["location"].available
    ):
        _lts_enabled, _lts_floor = _location_trend_quality_scaling_params()
        if _lts_enabled:
            _trend_quality = _clamp01(trend.quality) if trend.available else 0.0
            _lts_scale = _lts_floor + (1.0 - _lts_floor) * _trend_quality
            _lts_before = components["location"].quality
            location = Component(
                components["location"].signal,
                _clamp01(_lts_before * _lts_scale),
                available=True,
                directional=False,
            )
            components["location"] = location
            if "location" in active:
                active["location"] = location
            location_trend_scale_diag = {
                "trendQuality": round(_trend_quality, 4),
                "floor": round(_lts_floor, 4),
                "scale": round(_lts_scale, 4),
                "qualityBefore": round(_lts_before, 4),
                "qualityAfter": round(location.quality, 4),
            }

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
    equity_volume_floor_diag = {"enabled": False, "applied": False, "reason": ""}
    if family == "equity_etf" and decision == "TRADE":
        try:
            from config import CONFIG

            vol_cfg = CONFIG.get("ENGINE_A_V3_EQUITY_VOLUME_FLOOR") or {}
            if vol_cfg.get("ENABLED", True):
                equity_volume_floor_diag["enabled"] = True
                # 2026-08-09 (S1): groups with no approved live volume source
                # can never satisfy the floor under provenance enforcement.
                # Skip it (their volume weight is 0.0) instead of blanket-
                # blocking TRADE on data that is structurally unavailable.
                _skip_no_source = bool(vol_cfg.get("SKIP_WHEN_NO_APPROVED_SOURCE", True))
                if _skip_no_source and not _allowed_volume_sources(group):
                    equity_volume_floor_diag["reason"] = "no_approved_live_volume_source"
                else:
                    min_q = float(vol_cfg.get("MIN_QUALITY", 0.15))
                    if (not volume.available) or volume.quality < min_q:
                        decision = "WATCH"
                        equity_volume_blocked = True
                        equity_volume_floor_diag["applied"] = True
                        equity_volume_floor_diag["reason"] = "volume_quality_below_floor"
        except Exception:
            decision = "WATCH"
            equity_volume_blocked = True
            equity_volume_floor_diag["applied"] = True
            equity_volume_floor_diag["reason"] = "error"

    # ── Session / Spread / Correlation gates ───────────────────────────
    # These demote TRADE→WATCH when enabled. The session multiplier is keyed to
    # the entry candle's timestamp (never wall clock) and to the exchange the
    # group actually trades on; the spread multiplier is inert without a live
    # spread; the correlation multiplier is inert without scan cluster context.
    _entry_bar_time = None
    if entry_candles and isinstance(entry_candles[-1], dict):
        _entry_bar_time = entry_candles[-1].get("time") or entry_candles[-1].get("datetime")
    session_mult, session_gate_detail = _v3_session_multiplier(
        group,
        family,
        bar_time=_entry_bar_time,
        symbol=(context or {}).get("symbol") or (context or {}).get("display"),
    )
    spread_mult, spread_gate_detail = _v3_spread_multiplier(entry_snap, group, asset_type)
    correlation_mult, correlation_gate_detail = _v3_correlation_multiplier(
        context, group, family
    )
    pre_session_spread_confluence = confluence
    # Trigger-rung soft gate: when policy confirmation/trigger momentum is
    # available and opposes the scored direction (or is very weak), haircut
    # confluence so M15 is not purely decorative under the H4 momentum anchor.
    trigger_gate_mult = 1.0
    trigger_gate_detail: dict[str, Any] = {"enabled": False}
    try:
        from config import CONFIG

        from engine_a_v3.gate_toggles import entry_timing_gates_enabled

        tg_cfg = CONFIG.get("ENGINE_A_TRIGGER_EVIDENCE_SOFT_GATE") or {}
        if (
            entry_timing_gates_enabled(CONFIG)
            and bool(tg_cfg.get("ENABLED", True))
            and isinstance(trigger_evidence, Mapping)
            and trigger_evidence.get("available")
            and direction in ("LONG", "SHORT")
        ):
            t_sig = _f(trigger_evidence.get("signal"), 0.0) or 0.0
            t_q = _f(trigger_evidence.get("quality"), 0.0) or 0.0
            dsign_gate = 1.0 if direction == "LONG" else -1.0
            oppose_mult = float(tg_cfg.get("OPPOSE_MULT", 0.88))
            weak_max = float(tg_cfg.get("WEAK_QUALITY_MAX", 0.25))
            weak_mult = float(tg_cfg.get("WEAK_MULT", 0.94))
            if t_sig * dsign_gate < 0.0:
                trigger_gate_mult = _clamp(oppose_mult, 0.5, 1.0)
                trigger_gate_detail = {
                    "enabled": True,
                    "applied": True,
                    "reason": "trigger_opposes_direction",
                    "mult": trigger_gate_mult,
                    "triggerSignal": round(t_sig, 4),
                    "triggerQuality": round(t_q, 4),
                }
            elif t_q < weak_max:
                trigger_gate_mult = _clamp(weak_mult, 0.5, 1.0)
                trigger_gate_detail = {
                    "enabled": True,
                    "applied": True,
                    "reason": "trigger_weak_quality",
                    "mult": trigger_gate_mult,
                    "triggerSignal": round(t_sig, 4),
                    "triggerQuality": round(t_q, 4),
                }
            else:
                trigger_gate_detail = {
                    "enabled": True,
                    "applied": False,
                    "mult": 1.0,
                    "triggerSignal": round(t_sig, 4),
                    "triggerQuality": round(t_q, 4),
                }
    except Exception as exc:
        trigger_gate_detail = {"enabled": False, "error": str(exc)}

    gate_mult = session_mult * spread_mult * correlation_mult * trigger_gate_mult
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
        _spread_diag = _report_only_spread_diagnostic(entry_snap, group, asset_type)
        _corr_diag = dict(correlation_gate_detail)
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
            "locationTiming": location_timing_diag,
            "locationTrendScale": location_trend_scale_diag,
            "minDirectionalFailed": min_directional_failed,
            "legacyFilters": legacy_diag,
            "equityVolumeBlocked": equity_volume_blocked,
            "equityVolumeFloor": equity_volume_floor_diag,
            "cryptoDerivBlocked": crypto_deriv_blocked,
            "maxAttainableScore": max_attainable,
            "thresholdUnreachable": threshold_unreachable,
            "sessionGate": session_gate_detail,
            "spreadGate": spread_gate_detail,
            "correlationGate": correlation_gate_detail,
            "triggerEvidenceGate": trigger_gate_detail,
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
            "locationTiming": location_timing_diag,
            "locationTrendScale": location_trend_scale_diag,
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
                "trendHealthAdx": health_adx_tf if _health_adx_source != "off" else None,
                "trendHealthAdxSource": _health_adx_source,
                "trendStructureOnlyTfs": sorted(structure_only_tfs),
                **tf_diagnostics,
            },
            "m5Policy": policy_m5 or "disabled",
            "adxValue": mom_diag.get("adxValue"),
            "adxMultiplier": mom_diag.get("adxMultiplier"),
            "diAlignMult": mom_diag.get("diAlignMult"),
            "dirSum": round(dir_sum, 4),
            "directionalRampMult": round(dir_ramp_mult, 4),
            "minDirectionalFailed": min_directional_failed,
            # True when direction resolved FLAT purely because |dirSum| sat
            # inside directionDeadband — the case minDirectionalFailed cannot
            # report (see the comment at the direction resolution above).
            "directionDeadbandFlat": direction_deadband_flat,
            "directionDeadband": round(float(profile.direction_deadband), 6),
            "dirSumAbs": round(abs(dir_sum), 6),
            # Signed, weight-applied share of dirSum per factor. Makes an
            # opposing subsystem (carry/sentiment) visible when it cancels a
            # coherent trend instead of merely damping it.
            "dirContributions": dir_contributions,
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
            "volumeProvenance": volume_provenance,
            "weighting": {
                "baseCoreWeights": {
                    name: round(weights.get(name, 0.0), 4)
                    for name in CORE_COMPONENTS
                },
                "effectiveWeights": {
                    name: round(combined_weights.get(name, 0.0), 4)
                    for name in (*CORE_COMPONENTS, *SUBSYSTEM_FACTORS)
                },
                "coreScale": round(price_scale, 4),
                "subsystemBudget": round(sub_budget, 4),
                "subsystemWeightScope": sub_weight_scope,
                "subsystemMaxDirectionalShare": sub_max_share,
                "subsystemDirectionScale": round(sub_dir_scale, 4),
                "subsystemDirectionShare": round(
                    (sub_dir_raw * sub_dir_scale) / weight_sum, 4
                ),
            },
            "legacyFilters": legacy_diag,
            "trendState": legacy_diag.get("trendState"),
            "equityVolumeBlocked": equity_volume_blocked,
            "equityVolumeFloor": equity_volume_floor_diag,
            "cryptoDerivBlocked": crypto_deriv_blocked,
            "sessionGate": session_gate_detail,
            "spreadGate": spread_gate_detail,
            "correlationGate": correlation_gate_detail,
            "triggerEvidenceGate": trigger_gate_detail,
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
                    # Must mirror the `aligned` rule the confluence loop uses.
                    # Omitting the `directional` clause made non-directional
                    # location report a 0 contribution whenever its signal
                    # happened to oppose the trade side, even though it had
                    # contributed its full weight x quality to the score.
                    "contribution": round(
                        combined_weights.get(name, 0.0) * comp.quality * vol_mults.get(name, 1.0)
                        if (dsign and (not comp.directional or comp.signal * dsign > 0.0))
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
