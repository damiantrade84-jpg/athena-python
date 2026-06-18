"""Deterministic read-only timeframe routing for Engine A chart review."""

from __future__ import annotations

from typing import Any

SCHEMA_VERSION = "timeframe_route.v1"

DEFAULT_TIMEFRAME_ROUTING: dict[str, Any] = {
    "ENABLED": True,
    "DEFAULT": {
        "D1": {"entry_tf": "H4", "execution_tf": "H1"},
        "H4": {"entry_tf": "H1", "execution_tf": "M15"},
        "H1": {"entry_tf": "M15", "execution_tf": "M15"},
        "M15": {"entry_tf": "M15", "execution_tf": "M5"},
        "M5": {"entry_tf": "M5", "execution_tf": "M5"},
        "M1": {"entry_tf": "M1", "execution_tf": "M1"},
    },
    "BY_ASSET_GROUP": {
        "forex": {
            "H4": {"entry_tf": "H1", "execution_tf": "M15"},
            "H1": {"entry_tf": "M15", "execution_tf": "M15"},
        },
        "crypto": {
            "H4": {"entry_tf": "H1", "execution_tf": "M15"},
            "H1": {"entry_tf": "M15", "execution_tf": "M15"},
        },
        "commodities": {
            "H4": {"entry_tf": "H1", "execution_tf": "M15"},
            "H1": {"entry_tf": "M15", "execution_tf": "M15"},
        },
        "indices": {
            "H4": {"entry_tf": "H1", "execution_tf": "M15"},
            "H1": {"entry_tf": "M15", "execution_tf": "M15"},
        },
        "stocks": {
            "D1": {"entry_tf": "H4", "execution_tf": "H1"},
            "H4": {"entry_tf": "H1", "execution_tf": "M15"},
        },
    },
}

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


def _deep_merge(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in overrides.items():
        if isinstance(merged.get(key), dict) and isinstance(value, dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


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


def _route_config(cfg: dict[str, Any] | None) -> dict[str, Any]:
    if isinstance(cfg, dict) and isinstance(cfg.get("TIMEFRAME_ROUTING"), dict):
        cfg = cfg.get("TIMEFRAME_ROUTING")
    if not isinstance(cfg, dict):
        return dict(DEFAULT_TIMEFRAME_ROUTING)
    return _deep_merge(DEFAULT_TIMEFRAME_ROUTING, cfg)


def _route_for(
    cfg: dict[str, Any],
    *,
    asset_group: str,
    context_tf: str,
) -> dict[str, str]:
    group_routes = cfg.get("BY_ASSET_GROUP") or {}
    if isinstance(group_routes, dict):
        candidate = group_routes.get(asset_group)
        if isinstance(candidate, dict) and isinstance(candidate.get(context_tf), dict):
            route = candidate[context_tf]
            return {
                "entry_tf": normalize_timeframe(route.get("entry_tf") or context_tf),
                "execution_tf": normalize_timeframe(route.get("execution_tf") or route.get("entry_tf") or context_tf),
            }
    default_routes = cfg.get("DEFAULT") or {}
    if isinstance(default_routes, dict) and isinstance(default_routes.get(context_tf), dict):
        route = default_routes[context_tf]
        return {
            "entry_tf": normalize_timeframe(route.get("entry_tf") or context_tf),
            "execution_tf": normalize_timeframe(route.get("execution_tf") or route.get("entry_tf") or context_tf),
        }
    return {"entry_tf": context_tf, "execution_tf": context_tf}


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
        "engine_b_confirmed",
        "engine_b_direction_confirmed_entry_rejected",
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
    cfg: dict[str, Any] | None = None,
    primary_engine: str = "A",
) -> dict[str, Any]:
    """Resolve the read-only chart route from engine context and AI verdict."""
    routing_cfg = _route_config(cfg)
    enabled = bool(routing_cfg.get("ENABLED", True))
    source_group = str(asset_group or "").strip().lower() or "default"
    route_group = normalize_asset_route_group(source_group)
    context = normalize_timeframe(context_tf)
    route = _route_for(routing_cfg, asset_group=route_group, context_tf=context)
    entry = normalize_timeframe(route["entry_tf"])
    execution = normalize_timeframe(route["execution_tf"])

    comparison = verdict_comparison if isinstance(verdict_comparison, dict) else {}
    review = ai_review if isinstance(ai_review, dict) else {}
    direction_ok = _direction_confirmed(comparison)
    wait_action = _human_action(review, comparison) in {"wait", "watch"}
    needs_entry_wait = enabled and direction_ok and (_entry_rejected(comparison) or wait_action)

    auto_select = entry if needs_entry_wait else context
    mode = "entry_wait" if needs_entry_wait else "context"
    if not enabled:
        mode = "disabled"
        auto_select = context
        reason = f"Timeframe routing disabled; keeping {context} context chart selected."
    elif needs_entry_wait:
        reason = (
            f"{context} direction valid but AI rejected entry timing; "
            f"monitor {entry} for entry confirmation."
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
        "entryTf": entry,
        "executionTf": execution,
        "autoSelectTf": auto_select,
        "mode": mode,
        "reason": reason,
    }
