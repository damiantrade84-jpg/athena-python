"""Shared timeframe-policy resolution for the v3 backtester adapters.

Both Engine A and Engine B adapters resolve roles through the same
``timeframe_policy.resolve_timeframe_policy`` path live scoring uses, so
backtests cannot silently score on a different ladder than production.
"""

from __future__ import annotations

from typing import Any, Mapping


def resolve_backtest_policy_roles(
    pair: Mapping[str, Any],
    *,
    engine: str,
    style: str,
) -> dict[str, Any]:
    """Return role TFs + provenance for one (pair, engine, style) backtest."""
    from engine_a_groups import resolve_score_group_by_type
    from timeframe_policy import resolve_timeframe_policy

    engine_key = "engine_a" if str(engine).upper() in {"A", "ENGINE_A", "ENGINE_A_V3"} else "engine_b"
    symbol = str(pair.get("display") or pair.get("symbol") or "")
    asset_type = str(pair.get("type") or pair.get("asset_type") or "")
    score_group = resolve_score_group_by_type(dict(pair))
    horizon = str(style or "intraday").strip().lower() or "intraday"
    if horizon == "auto":
        horizon = "intraday"

    policy = resolve_timeframe_policy(
        symbol,
        asset_type,
        score_group,
        horizon,
        engine_id=engine_key,
    )
    roles = {
        "regime": policy.regime_tf.value,
        "bias": policy.bias_tf.value,
        "structure": policy.structure_tf.value,
        "setup": policy.setup_tf.value,
        "trigger": policy.trigger_tf.value,
        "execution": policy.execution_tf.value,
        "m5_policy": policy.m5_policy.value,
        "execution_prerequisite": (
            policy.execution_prerequisite_tf.value
            if policy.execution_prerequisite_tf
            else None
        ),
        "m5_role": policy.m5_role.value,
        "execution_mode": policy.execution_mode.value,
    }
    return {
        "roles": roles,
        "policyVersion": policy.policy_version,
        "policyKey": policy.policy_key,
        "policyProfile": policy.profile,
        "policySource": policy.policy_source.value,
        "scoreGroup": score_group,
        "style": policy.style,
        "engineId": policy.engine_id,
        "requiredTfs": list(
            dict.fromkeys(
                tf
                for tf in (
                    roles["regime"],
                    roles["bias"],
                    roles["structure"],
                    roles["setup"],
                    roles["trigger"],
                    roles["execution"],
                    roles.get("execution_prerequisite"),
                    "D1",
                    "H4",
                    "H1",
                    "M15",
                )
                if tf
            )
        ),
    }


def policy_timeframes_for_scorer(resolved: Mapping[str, Any]) -> dict[str, Any]:
    """Flatten roles into the mapping ``evaluate_engine_a_v3`` expects."""
    roles = dict(resolved.get("roles") or {})
    return {
        "regime": roles.get("regime"),
        "bias": roles.get("bias"),
        "structure": roles.get("structure"),
        "setup": roles.get("setup"),
        "trigger": roles.get("trigger"),
        "execution": roles.get("execution"),
        "m5_policy": roles.get("m5_policy"),
        "execution_prerequisite": roles.get("execution_prerequisite"),
    }


def attach_policy_provenance(result: dict[str, Any], resolved: Mapping[str, Any]) -> dict[str, Any]:
    """Stamp policy fields onto a backtest result for UI / persistence."""
    roles = dict(resolved.get("roles") or {})
    result["timeframePolicyVersion"] = resolved.get("policyVersion")
    result["policyKey"] = resolved.get("policyKey")
    result["timeframeProfile"] = resolved.get("policyProfile")
    result["policySource"] = resolved.get("policySource")
    result["scoreGroup"] = resolved.get("scoreGroup")
    result["regimeTf"] = roles.get("regime")
    result["biasTf"] = roles.get("bias")
    result["structureTf"] = roles.get("structure")
    result["setupTf"] = roles.get("setup")
    result["triggerTf"] = roles.get("trigger")
    result["executionTf"] = roles.get("execution")
    result["m5Policy"] = roles.get("m5_policy")
    result["m5Role"] = roles.get("m5_role")
    result["executionMode"] = roles.get("execution_mode")
    result["requiredPolicyTfs"] = list(resolved.get("requiredTfs") or [])
    return result
