"""V3 directional-ramp thresholds calibrated for quant_scorer dir_sum scale.

V2 factor_scoring uses trend_score (~±1). V3 dir_sum is a weighted mean of
signal×quality over active components and typically runs an order of magnitude
smaller. Never reuse ENGINE_A_DIRECTIONAL_RAMP_BY_CLASS for V3 scoring.
"""

from __future__ import annotations

from typing import Any

_FAMILY_ASSET = {
    "forex": "forex",
    "crypto": "crypto",
    "commodity": "commodity",
    "index": "index",
    "equity_etf": "stock",
    "unknown": "other",
}

# Conservative V3 defaults: abort only near-deadband noise; soft ramp above that.
_V3_DEFAULTS_BY_ASSET: dict[str, dict[str, float]] = {
    "crypto": {"min_directional": 0.04, "soft_span": 0.06},
    "forex": {"min_directional": 0.05, "soft_span": 0.07},
    "stock": {"min_directional": 0.06, "soft_span": 0.08},
    "index": {"min_directional": 0.05, "soft_span": 0.07},
    "commodity": {"min_directional": 0.05, "soft_span": 0.08},
    "other": {"min_directional": 0.05, "soft_span": 0.07},
}


def _float_cfg(value: Any, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if parsed != parsed:  # NaN
        return default
    return parsed


def _resolve_class_keyed(mapping: dict, score_group: str, asset_type: str, default: Any) -> Any:
    if not isinstance(mapping, dict):
        return default
    if score_group and score_group in mapping:
        return mapping[score_group]
    if asset_type in mapping:
        return mapping[asset_type]
    if "default" in mapping:
        return mapping["default"]
    return default


def resolve_v3_directional_ramp(asset_type: str, score_group: str) -> tuple[float, float]:
    """Return (min_directional, soft_span) for Engine A V3 quant dir_sum."""
    defaults = dict(_V3_DEFAULTS_BY_ASSET.get(asset_type, _V3_DEFAULTS_BY_ASSET["other"]))
    try:
        from config import CONFIG

        keyed = CONFIG.get("ENGINE_A_V3_DIRECTIONAL_RAMP_BY_CLASS") or {}
        overrides = _resolve_class_keyed(keyed, score_group, asset_type, {})
        if isinstance(overrides, dict):
            defaults["min_directional"] = _float_cfg(
                overrides.get("min_directional"), defaults["min_directional"]
            )
            defaults["soft_span"] = _float_cfg(
                overrides.get("soft_span"), defaults["soft_span"]
            )
    except Exception:
        pass
    min_dir = max(0.0, defaults["min_directional"])
    soft_span = max(0.0, defaults["soft_span"])
    return min_dir, soft_span


def family_asset_type(family: str) -> str:
    return _FAMILY_ASSET.get(family, "other")
