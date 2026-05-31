"""Engine A trade-eligibility gate.

This module gates whether an already-scored Engine A signal may become
trade-ready. It must not alter scoring, direction, sizing, SL/TP, or risk checks.
"""

from __future__ import annotations

from typing import Any

from config import CONFIG


_DISABLED_WARNING_PREFIX = "ENGINE_A_RESEARCH_ONLY"


def _norm(value: Any) -> str:
    return str(value or "").strip()


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on", "enabled"}:
        return True
    if text in {"0", "false", "no", "off", "disabled"}:
        return False
    return default


def _lookup_bool(mapping: Any, keys: list[str]) -> tuple[bool | None, str | None]:
    if not isinstance(mapping, dict):
        return None, None
    for key in keys:
        if not key:
            continue
        if key in mapping:
            return _as_bool(mapping.get(key), False), key
    return None, None


def resolve_engine_a_trade_eligibility(
    pair: dict | None,
    signal: dict | None = None,
    *,
    config: dict | None = None,
) -> dict:
    """Return Engine A trade-eligibility detail for a scored signal.

    Disabled signals remain valid research signals, but they must not be promoted
    to trade-ready status by scanner, Engine C, or manual execution helpers.
    """
    cfg = config if isinstance(config, dict) else CONFIG
    if not _as_bool(cfg.get("ENGINE_A_TRADE_ELIGIBILITY_ENABLED", True), True):
        return {
            "enabled": True,
            "research_only": False,
            "source": "master_disabled",
            "reason": "Engine A trade-eligibility gate disabled; legacy trade eligibility applies.",
        }

    pair = pair or {}
    signal = signal or {}
    display = _norm(
        signal.get("display")
        or signal.get("pair")
        or pair.get("display")
        or pair.get("pair")
    )
    symbol = _norm(signal.get("symbol") or pair.get("symbol"))
    asset_type = _norm(signal.get("type") or pair.get("type")).lower()
    score_group = _norm(
        signal.get("scoreGroup")
        or signal.get("score_group")
        or pair.get("scoreGroup")
        or pair.get("score_group")
    )

    override_value, override_key = _lookup_bool(
        cfg.get("ENGINE_A_TRADE_ENABLED_OVERRIDES"),
        [display, symbol],
    )
    if override_value is not None:
        source = f"override:{override_key}"
        enabled = override_value
    else:
        group_value, group_key = _lookup_bool(
            cfg.get("ENGINE_A_TRADE_ENABLED_BY_SCORE_GROUP"),
            [score_group],
        )
        if group_value is not None:
            source = f"score_group:{group_key}"
            enabled = group_value
        else:
            class_value, class_key = _lookup_bool(
                cfg.get("ENGINE_A_TRADE_ENABLED_BY_CLASS"),
                [asset_type],
            )
            if class_value is not None:
                source = f"class:{class_key}"
                enabled = class_value
            else:
                enabled = _as_bool(cfg.get("ENGINE_A_TRADE_ENABLED_DEFAULT", False), False)
                source = "default"

    if enabled:
        reason = f"Engine A trade eligible by {source}."
    else:
        label = display or symbol or score_group or asset_type or "instrument"
        reason = (
            f"Engine A research-only for {label}: trend engine disabled by {source} "
            "until n>=30 and SQN>2.0 evidence qualifies it."
        )
    return {
        "enabled": bool(enabled),
        "research_only": not bool(enabled),
        "source": source,
        "reason": reason,
        "display": display or None,
        "symbol": symbol or None,
        "score_group": score_group or None,
        "asset_type": asset_type or None,
    }


def engine_a_trade_enabled(
    pair: dict | None,
    signal: dict | None = None,
    *,
    config: dict | None = None,
) -> bool:
    return bool(resolve_engine_a_trade_eligibility(pair, signal, config=config)["enabled"])


def annotate_engine_a_trade_eligibility(
    signal: dict,
    pair: dict | None,
    *,
    config: dict | None = None,
) -> dict:
    detail = resolve_engine_a_trade_eligibility(pair, signal, config=config)
    signal["engineATradeEnabled"] = bool(detail["enabled"])
    signal["engineATradeGate"] = detail
    if not detail["enabled"]:
        signal["trade"] = False
        signal["executable"] = False
        warnings = signal.setdefault("warnings", [])
        if isinstance(warnings, list):
            warning = f"{_DISABLED_WARNING_PREFIX}: {detail['reason']}"
            if warning not in warnings:
                warnings.append(warning)
    return signal
