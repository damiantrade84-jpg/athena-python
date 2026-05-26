"""Engine A scoring profile: trend TF stack, style, and momentum/regime anchors.

Resolves config-gated ENGINE_A_SCORING_PROFILE entries for use in factor_scoring
and AI chart-review context. Defaults preserve the historical D1/H4/H1 contract.
"""

from __future__ import annotations

from typing import Any

from config import CONFIG

_DEFAULT_LAYERS: tuple[dict[str, str], ...] = (
    {"tf": "D1", "weight_key": "d1_ema_trend", "fast_ema": "ema21", "slow_ema": "ema200"},
    {"tf": "H4", "weight_key": "h4_ema_trend", "fast_ema": "ema21", "slow_ema": "ema50"},
    {"tf": "H1", "weight_key": "ema_trend", "fast_ema": "ema21", "slow_ema": "ema50"},
)

_DEFAULT_TREND_WEIGHTS = {
    "d1_ema_trend": 0.50,
    "h4_ema_trend": 0.30,
    "ema_trend": 0.20,
}

_STYLE_TREND_WEIGHTS: dict[str, dict[str, float]] = {
    "swing": {"d1_ema_trend": 0.50, "h4_ema_trend": 0.30, "ema_trend": 0.20},
    "intraday": {"d1_ema_trend": 0.42, "h4_ema_trend": 0.33, "ema_trend": 0.25},
    "scalp": {"d1_ema_trend": 0.25, "h4_ema_trend": 0.35, "ema_trend": 0.40},
}

_STYLE_EXECUTION_TF = {
    "scalp": "H1",
    "intraday": "H4",
    "swing": "D1",
}

_STYLE_CHART_TF = {
    "scalp": "H1",
    "intraday": "H4",
    "swing": "D1",
}


def _normalize_style(style: str | None) -> str:
    s = str(style or "swing").strip().lower()
    if s == "auto":
        return "intraday"
    if s in ("scalp", "intraday", "swing"):
        return s
    return "swing"


def _profile_cfg() -> dict[str, Any]:
    raw = CONFIG.get("ENGINE_A_SCORING_PROFILE") or {}
    return raw if isinstance(raw, dict) else {}


def scoring_profile_enabled() -> bool:
    return bool(_profile_cfg().get("ENABLED", True))


def _merge_mapping(base: dict, override: dict | None) -> dict:
    out = dict(base or {})
    if isinstance(override, dict):
        out.update(override)
    return out


def _resolve_block(
    mapping: dict | None,
    *,
    score_group: str | None,
    asset_type: str,
) -> dict | None:
    if not isinstance(mapping, dict):
        return None
    if score_group and score_group in mapping and isinstance(mapping[score_group], dict):
        return mapping[score_group]
    if asset_type and asset_type in mapping and isinstance(mapping[asset_type], dict):
        return mapping[asset_type]
    default = mapping.get("default")
    return default if isinstance(default, dict) else None


def resolve_engine_a_scoring_profile(
    *,
    score_group: str | None,
    asset_type: str,
    style: str | None,
) -> dict[str, Any]:
    """Return resolved trend layers, weights, and anchor TFs for Engine A scoring."""
    resolved_style = _normalize_style(style)
    cfg = _profile_cfg()
    default_block = _resolve_block(cfg.get("DEFAULT_BY_CLASS"), score_group=score_group, asset_type=asset_type) or {}
    style_block = _resolve_block(
        (cfg.get("BY_STYLE") or {}),
        score_group=resolved_style,
        asset_type=resolved_style,
    ) or {}
    group_block = _resolve_block(
        (cfg.get("BY_SCORE_GROUP") or {}),
        score_group=score_group,
        asset_type=asset_type,
    ) or {}

    layers_raw = (
        group_block.get("trend_layers")
        or style_block.get("trend_layers")
        or default_block.get("trend_layers")
    )
    if isinstance(layers_raw, list) and layers_raw:
        layers = [dict(layer) for layer in layers_raw if isinstance(layer, dict) and layer.get("tf")]
    else:
        layers = [dict(layer) for layer in _DEFAULT_LAYERS]

    trend_weights = dict(_DEFAULT_TREND_WEIGHTS)
    trend_weights = _merge_mapping(trend_weights, _STYLE_TREND_WEIGHTS.get(resolved_style))
    trend_weights = _merge_mapping(
        trend_weights,
        group_block.get("trend_weights") or style_block.get("trend_weights") or default_block.get("trend_weights"),
    )

    momentum_tf = str(
        group_block.get("momentum_tf")
        or style_block.get("momentum_tf")
        or default_block.get("momentum_tf")
        or "H4"
    ).upper()
    regime_tf = str(
        group_block.get("regime_tf")
        or style_block.get("regime_tf")
        or default_block.get("regime_tf")
        or momentum_tf
    ).upper()
    execution_tf = str(
        group_block.get("execution_tf")
        or style_block.get("execution_tf")
        or default_block.get("execution_tf")
        or _STYLE_EXECUTION_TF.get(resolved_style, "H4")
    ).upper()
    chart_tf = str(
        group_block.get("chart_tf")
        or style_block.get("chart_tf")
        or default_block.get("chart_tf")
        or _STYLE_CHART_TF.get(resolved_style, momentum_tf)
    ).upper()

    trend_timeframes = [str(layer.get("tf")).upper() for layer in layers]

    return {
        "enabled": scoring_profile_enabled(),
        "style": resolved_style,
        "score_group": score_group,
        "asset_type": asset_type,
        "trend_layers": layers,
        "trend_weights": trend_weights,
        "trend_timeframes": trend_timeframes,
        "momentum_tf": momentum_tf,
        "regime_tf": regime_tf,
        "execution_tf": execution_tf,
        "chart_tf": chart_tf,
    }


def scoring_profile_public_dict(profile: dict[str, Any] | None) -> dict[str, Any]:
    """Trim profile for signal payloads and AI context."""
    if not isinstance(profile, dict):
        return {}
    return {
        "enabled": bool(profile.get("enabled")),
        "style": profile.get("style"),
        "scoreGroup": profile.get("score_group"),
        "assetType": profile.get("asset_type"),
        "trendTimeframes": list(profile.get("trend_timeframes") or []),
        "momentumTf": profile.get("momentum_tf"),
        "regimeTf": profile.get("regime_tf"),
        "executionTf": profile.get("execution_tf"),
        "chartTf": profile.get("chart_tf"),
        "trendWeights": dict(profile.get("trend_weights") or {}),
    }


def snap_for_tf(
    profile: dict[str, Any],
    *,
    d1_snap: dict,
    h4_snap: dict,
    h1_snap: dict,
    tf: str,
) -> dict:
    tf_u = str(tf or "").upper()
    mapping = {
        "D1": d1_snap,
        "H4": h4_snap,
        "H1": h1_snap,
    }
    return mapping.get(tf_u) or {}
