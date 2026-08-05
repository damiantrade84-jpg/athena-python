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
- Engine B gates       -> engine_b_snapshot.resolve_engine_b_style_profile (the
                          exact resolver live scoring uses, which merges
                          NAKED_ENGINE.score_group_overrides[group][style] over
                          the style profile), so the UI shows the real live
                          numbers/bools. space_rr_substitute stays null when
                          unset (the global asset-scoped flag decides there).
                          The two SL-width clamps (min_sl_atr / max_sl_atr)
                          resolve from ENGINE_B_ATR_SL_CLAMP_SCORE_GROUP_OVERRIDES[group]
                          with the global ENGINE_B_MIN/MAX_SL_ATR_DEFAULT
                          fallback, so the UI shows the genuinely live clamp.
- Indicator weights    -> engine_a_v3.quant_scorer._group_scoped_blend_weight /
                          engine_b_quality.weighted_scoring_config_for_group
                          (same per-group-with-global-fallback resolution the
                          scoring core itself uses; groups an operator has not
                          tuned fall back to the process-wide value, 0.0)
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


def _engine_b_gate_values(pair: Mapping[str, Any], group: str, style: str) -> dict[str, Any]:
    """Currently live Engine B gate values for this (group, style, pair).

    Resolved through the exact style-profile resolver live scoring uses
    (engine_b_snapshot.resolve_engine_b_style_profile), which already merges
    NAKED_ENGINE.score_group_overrides[group][style] over the style profile —
    so the UI shows the real numbers/bools scoring runs on today, not a bare
    "no override" placeholder.
    """
    from config import CONFIG
    from engine_b_snapshot import resolve_engine_b_style_profile

    asset_type = str(pair.get("type") or pair.get("asset_type") or "")
    symbol = str(pair.get("display") or pair.get("symbol") or "")
    try:
        _resolved, profile = resolve_engine_b_style_profile(
            style, group, asset_type, symbol=symbol
        )
    except Exception:
        profile = {}

    def _num(field: str) -> float | None:
        raw = profile.get(field)
        try:
            return None if raw is None else float(raw)
        except (TypeError, ValueError):
            return None

    values: dict[str, Any] = {
        "engine_b.gate.min_score": _num("min_score"),
        "engine_b.gate.min_rr": _num("min_rr"),
        "engine_b.gate.min_room_atr": _num("min_room_atr"),
        # macro_required's consumer defaults to False when the key is absent
        # (market_structure.py DXY gate), so False IS the live value.
        "engine_b.gate.macro_required": bool(profile.get("macro_required", False)),
        # space_rr_substitute has no per-(group,style) default: unset means the
        # global asset-scoped space-gate flag decides. Leave None so the UI can
        # honestly show "no override".
        "engine_b.gate.space_rr_substitute": profile.get("space_rr_substitute"),
        "engine_b.gate.profile_trusted": profile.get("profile_trusted"),
    }

    # profile_trusted absent from the profile -> the global trust-list
    # resolution decides (same call the scoring path makes).
    if values["engine_b.gate.profile_trusted"] is None:
        try:
            from market_structure import engine_b_profile_context_enabled

            values["engine_b.gate.profile_trusted"] = bool(
                engine_b_profile_context_enabled(asset_type, CONFIG, score_group=group)
            )
        except Exception:
            values["engine_b.gate.profile_trusted"] = None

    # SL-width ATR clamps live on a different, flat per-group surface — the
    # only one market_structure.py's clamp/tighten/TP1-retry paths read.
    # Show the genuinely live value: group override -> global default key ->
    # the code fallback (0.75 / 3.0).
    group_key = str(group or "").strip().lower()
    clamp_overrides = CONFIG.get("ENGINE_B_ATR_SL_CLAMP_SCORE_GROUP_OVERRIDES")
    clamp_row: Mapping[str, Any] = {}
    if isinstance(clamp_overrides, Mapping):
        candidate = clamp_overrides.get(group_key)
        if isinstance(candidate, Mapping):
            clamp_row = candidate

    def _clamp_value(field: str, default_key: str, fallback: float) -> float:
        raw = clamp_row.get(field, CONFIG.get(default_key, fallback))
        try:
            return float(raw)
        except (TypeError, ValueError):
            return fallback

    values["engine_b.gate.min_sl_atr"] = _clamp_value(
        "min_sl_atr", "ENGINE_B_MIN_SL_ATR_DEFAULT", 0.75
    )
    values["engine_b.gate.max_sl_atr"] = _clamp_value(
        "max_sl_atr", "ENGINE_B_MAX_SL_ATR_DEFAULT", 3.0
    )
    return values


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
        values.update(_engine_b_gate_values(pair, group, style))
        values.update(_engine_b_indicator_values(group))
    return values
