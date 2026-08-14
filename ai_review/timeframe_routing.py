"""Deterministic read-only timeframe routing for Engine A chart review."""

from __future__ import annotations

from typing import Any

SCHEMA_VERSION = "timeframe_route.v2"
_POLICY_TIMEFRAMES = frozenset({"M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1"})

_TF_ALIASES = {
    "1": "M1",
    "1M": "M1",
    "M1": "M1",
    "5": "M5",
    "5M": "M5",
    "M5": "M5",
    "15": "M15",
    "15M": "M15",
    "M15": "M15",
    "30": "M30",
    "30M": "M30",
    "M30": "M30",
    "60": "H1",
    "1H": "H1",
    "H1": "H1",
    "240": "H4",
    "4H": "H4",
    "H4": "H4",
    "D": "D1",
    "1D": "D1",
    "D1": "D1",
    "DAY": "D1",
    "DAILY": "D1",
    "W": "W1",
    "1W": "W1",
    "W1": "W1",
    "WEEK": "W1",
    "WEEKLY": "W1",
}

_ASSET_GROUP_ALIASES = {
    "forex": "forex",
    "forex_majors": "forex",
    "forex_crosses": "forex",
    "forex_exotics": "forex",
    "forex_other": "forex",
    "crypto": "crypto",
    "crypto_btc": "crypto",
    "crypto_eth": "crypto",
    "crypto_doge": "crypto",
    "crypto_alt_majors": "crypto",
    "crypto_other": "crypto",
    "commodity": "commodities",
    "commodities": "commodities",
    "precious_trackers": "commodities",
    "energy_oil": "commodities",
    "nat_gas": "commodities",
    "copper": "commodities",
    "pgm_metals": "commodities",
    "base_metals": "commodities",
    "softs": "commodities",
    "commodity_other": "commodities",
    "index": "indices",
    "indices": "indices",
    "us_indices_trackers": "indices",
    "eu_indices": "indices",
    "asian_indices": "indices",
    "index_other": "indices",
    "stock": "stocks",
    "stocks": "stocks",
    "etf": "stocks",
    "us_stock_single": "stocks",
    "bond_tlt": "stocks",
    "smallcap_em_etf": "stocks",
    "stock_other": "stocks",
}


def normalize_timeframe(raw: Any) -> str:
    """Return the canonical backend timeframe string used by chart review."""
    text = str(raw or "").strip().upper().replace(" ", "")
    if not text:
        return "H4"
    if text in _TF_ALIASES:
        return _TF_ALIASES[text]
    if text.endswith("H") and text[:-1].isdigit():
        return f"H{text[:-1]}"
    if text.endswith("M") and text[:-1].isdigit():
        return f"M{text[:-1]}"
    return text


def normalize_asset_route_group(raw: Any) -> str:
    """Collapse score-group and raw type aliases into broad route groups."""
    key = str(raw or "").strip().lower()
    if not key:
        return "default"
    return _ASSET_GROUP_ALIASES.get(key, "default")


def _policy_tf(policy: dict[str, Any], camel: str, snake: str) -> str | None:
    value = policy.get(camel) or policy.get(snake)
    if value in (None, ""):
        return None
    normalized = normalize_timeframe(value)
    return normalized if normalized in _POLICY_TIMEFRAMES else None


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value or "").strip().lower() in {"true", "yes", "1"}


def _direction_confirmed(comparison: dict[str, Any]) -> bool:
    verdict = str(comparison.get("comparisonVerdict") or "").strip()
    if verdict in {
        "engine_a_confirmed",
        "engine_a_direction_confirmed_entry_rejected",
        "engine_a_direction_confirmed_wait",
        "engine_b_confirmed",
        "engine_b_direction_confirmed_entry_rejected",
        "engine_b_direction_confirmed_wait",
    }:
        return True
    if _truthy(comparison.get("chartConfirmsEngineBDirection")):
        return comparison.get("engineBProvided") is not False
    if _truthy(comparison.get("chartConfirmsEngineADirection")):
        return comparison.get("engineAProvided") is not False
    return False


def _entry_rejected(comparison: dict[str, Any]) -> bool:
    verdict = str(comparison.get("comparisonVerdict") or "").strip()
    if verdict in {
        "engine_a_direction_confirmed_entry_rejected",
        "engine_b_direction_confirmed_entry_rejected",
    }:
        return True
    return _truthy(comparison.get("chartContradictsEntryTiming"))


def _human_action(ai_review: dict[str, Any], comparison: dict[str, Any]) -> str:
    return str(
        ai_review.get("human_action")
        or ai_review.get("humanAction")
        or comparison.get("finalDecision")
        or ""
    ).strip().lower()


def resolve_timeframe_route(
    *,
    asset_group: Any,
    context_tf: Any,
    ai_review: dict[str, Any] | None = None,
    verdict_comparison: dict[str, Any] | None = None,
    policy_roles: dict[str, Any] | None = None,
    primary_engine: str = "A",
) -> dict[str, Any]:
    """Resolve chart navigation from the signal's authoritative policy-v4 roles."""
    source_group = str(asset_group or "").strip().lower() or "default"
    route_group = normalize_asset_route_group(source_group)
    context = normalize_timeframe(context_tf)
    policy = policy_roles if isinstance(policy_roles, dict) else {}
    regime = _policy_tf(policy, "regimeTf", "regime_tf")
    bias = _policy_tf(policy, "biasTf", "bias_tf")
    structure = _policy_tf(policy, "structureTf", "structure_tf")
    setup = _policy_tf(policy, "setupTf", "setup_tf")
    trigger = _policy_tf(policy, "triggerTf", "trigger_tf")
    execution = _policy_tf(policy, "executionTf", "execution_tf")
    policy_available = all((regime, bias, structure, setup, trigger, execution))
    enabled = bool(policy_available)

    comparison = verdict_comparison if isinstance(verdict_comparison, dict) else {}
    review = ai_review if isinstance(ai_review, dict) else {}
    direction_ok = _direction_confirmed(comparison)
    wait_action = _human_action(review, comparison) in {"wait", "watch"}
    needs_entry_wait = enabled and direction_ok and (_entry_rejected(comparison) or wait_action)

    auto_select = trigger if needs_entry_wait and trigger else context
    mode = "entry_wait" if needs_entry_wait else "context"
    if not enabled:
        mode = "policy_unavailable"
        auto_select = context
        reason = (
            "Authoritative timeframe-policy roles are unavailable; "
            f"keeping {context} selected without a legacy fallback."
        )
    elif needs_entry_wait:
        reason = (
            f"{context} direction remains valid but the advisory AI review withheld "
            f"immediate entry; showing the policy trigger timeframe {trigger}."
        )
    else:
        reason = f"No deterministic entry-wait condition; keeping {context} context chart selected."

    return {
        "schemaVersion": SCHEMA_VERSION,
        "enabled": enabled,
        "engine": "B" if str(primary_engine or "A").upper() == "B" else "A",
        "assetGroup": route_group,
        "sourceGroup": source_group,
        "contextTf": context,
        "regimeTf": regime,
        "biasTf": bias,
        "structureTf": structure,
        "setupTf": setup,
        "triggerTf": trigger,
        "entryTf": setup,
        "executionTf": execution,
        "executionMode": str(
            policy.get("executionMode") or policy.get("execution_mode") or ""
        ).strip().lower() or None,
        "m5Role": policy.get("m5Role") or policy.get("m5_role"),
        "m5Policy": policy.get("m5Policy") or policy.get("m5_policy"),
        "policyVersion": policy.get("timeframePolicyVersion") or policy.get("policy_version"),
        "policyKey": policy.get("policyKey") or policy.get("policy_key"),
        "routeSource": "timeframe_policy" if policy_available else "unavailable",
        "autoSelectTf": auto_select,
        "mode": mode,
        "reason": reason,
    }
