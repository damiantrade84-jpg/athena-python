"""Resolve the CURRENTLY ACTIVE value for every Tuning Lab knob.

The knob catalog (knobs.py) cannot embed a single static "default" for most
knobs — Engine A component weights and trade thresholds vary per score group,
TF roles vary per (group, style, engine), and Engine B gate fields are either
overridden per (group, style) or fall back to a style profile with no single
constant. Guessing a number here would be worse than showing nothing, so
every value below is read through the exact resolver live scoring uses:

- TF roles            -> timeframe_policy.resolve_timeframe_policy (same call
                          athena_backtest/policy.py makes for a real backtest)
- Engine A weights     -> engine_a_v3.profile._resolved_weights (same call
                          EngineAV3Profile.create makes)
- Engine A threshold   -> engine_a_v3.profile._resolved_trade_threshold
- Engine B gates       -> NAKED_ENGINE.score_group_overrides[group][style]
                          (returns null when unset -- there is no single
                          "default" number without a live style profile, so
                          the UI must show "no override" rather than a guess)
- Indicator weights    -> engine_a_v3.quant_scorer._group_scoped_blend_weight /
                          engine_b_quality.weighted_scoring_config_for_group
                          (same per-group-with-global-fallback resolution the
                          scoring core itself uses; today every one of them is
                          genuinely 0.0 because none of these keys exist in
                          config.yaml/config.local.yaml yet, at any group)
"""

from __future__ import annotations

from typing import Any, Mapping


def _engine_a_weight_and_threshold_values(group: str) -> dict[str, Any]:
    from engine_a_v3.profile import _family_for, _resolved_trade_threshold, _resolved_weights

    family = _family_for(group)
    weights = _resolved_weights(group, family)
    values = {f"engine_a.weight.{component}": weights.get(component) for component in weights}
    values["engine_a.trade_threshold"] = _resolved_trade_threshold(group)
    return values


def _engine_b_gate_values(group: str, style: str) -> dict[str, Any]:
    from config import CONFIG

    fields = (
        "min_score", "min_rr", "min_room_atr", "max_sl_atr", "min_sl_atr",
        "space_rr_substitute", "profile_trusted", "macro_required",
    )
    naked_engine = CONFIG.get("NAKED_ENGINE")
    overrides: Mapping[str, Any] = {}
    if isinstance(naked_engine, Mapping):
        group_overrides = naked_engine.get("score_group_overrides")
        if isinstance(group_overrides, Mapping):
            group_row = group_overrides.get(str(group or "").strip().lower())
            if isinstance(group_row, Mapping):
                style_row = group_row.get(str(style or "").strip().lower())
                if isinstance(style_row, Mapping):
                    overrides = style_row
    return {f"engine_b.gate.{field}": overrides.get(field) for field in fields}


def _tf_role_values(pair: Mapping[str, Any], group: str, style: str, engine: str) -> dict[str, Any]:
    from timeframe_policy import resolve_timeframe_policy

    symbol = str(pair.get("display") or pair.get("symbol") or "")
    asset_type = str(pair.get("type") or pair.get("asset_type") or "")
    engine_key = "engine_a" if str(engine).upper() == "A" else "engine_b"
    policy = resolve_timeframe_policy(symbol, asset_type, group, style, engine_id=engine_key)
    return {
        "tf_role.regime": policy.regime_tf.value,
        "tf_role.bias": policy.bias_tf.value,
        "tf_role.structure": policy.structure_tf.value,
        "tf_role.setup": policy.setup_tf.value,
        "tf_role.trigger": policy.trigger_tf.value,
    }


def _engine_a_indicator_values(group: str) -> dict[str, Any]:
    from config import CONFIG
    from engine_a_v3.quant_scorer import _group_scoped_blend_weight

    momentum_cfg = CONFIG.get("ENGINE_A_V3_MOMENTUM_BLEND") or {}
    location_cfg = CONFIG.get("ENGINE_A_V3_LOCATION") or {}
    volume_cfg = CONFIG.get("ENGINE_A_V3_VOLUME_BLEND") or {}
    return {
        "indicator.momentum.stoch_weight": _group_scoped_blend_weight(momentum_cfg, group, "STOCH_WEIGHT", 0.0),
        "indicator.momentum.cci_weight": _group_scoped_blend_weight(momentum_cfg, group, "CCI_WEIGHT", 0.0),
        "indicator.momentum.williams_r_weight": _group_scoped_blend_weight(momentum_cfg, group, "WILLIAMS_R_WEIGHT", 0.0),
        "indicator.momentum.roc_weight": _group_scoped_blend_weight(momentum_cfg, group, "ROC_WEIGHT", 0.0),
        "indicator.location.keltner_weight": _group_scoped_blend_weight(location_cfg, group, "KELTNER_BLEND_WEIGHT", 0.0),
        "indicator.volume.mfi_weight": _group_scoped_blend_weight(volume_cfg, group, "MFI_WEIGHT", 0.0),
    }


def _engine_b_indicator_values(group: str) -> dict[str, Any]:
    from engine_b_quality import weighted_scoring_config_for_group

    cfg = weighted_scoring_config_for_group(group)
    weights = cfg.get("COMPONENT_WEIGHTS") or {}
    try:
        value = float(weights.get("momentum_oscillator_confluence", 0.0))
    except (TypeError, ValueError):
        value = 0.0
    return {"indicator.engine_b.momentum_oscillator_weight": value}


def resolve_current_values(engine: str, pair: Mapping[str, Any], group: str, style: str) -> dict[str, Any]:
    """Return ``{knob_id: current_live_value}`` for every knob that applies to
    ``engine``, resolved for this specific ``pair``/``group``/``style``."""
    engine_u = str(engine or "A").strip().upper()
    values: dict[str, Any] = {}
    values.update(_tf_role_values(pair, group, style, engine_u))
    if engine_u == "A":
        values.update(_engine_a_weight_and_threshold_values(group))
        values.update(_engine_a_indicator_values(group))
    else:
        values.update(_engine_b_gate_values(group, style))
        values.update(_engine_b_indicator_values(group))
    return values
