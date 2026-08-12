"""Engine A trade-eligibility gate.

This module gates whether an already-scored Engine A signal may become
trade-ready. It must not alter scoring, direction, sizing, SL/TP, or risk checks.
"""

from __future__ import annotations

import logging
from typing import Any

from config import CONFIG


log = logging.getLogger("athena")
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


def _lookup_mapping(mapping: Any, keys: list[str]) -> tuple[dict | None, str | None]:
    if not isinstance(mapping, dict):
        return None, None
    for key in keys:
        if key and isinstance(mapping.get(key), dict):
            return mapping.get(key), key
    return None, None


def _evidence_qualified(cfg: dict, keys: list[str]) -> tuple[bool, str]:
    evidence, key = _lookup_mapping(cfg.get("ENGINE_A_TRADE_ENABLED_EVIDENCE"), keys)
    if evidence is None:
        return False, "evidence_missing"
    try:
        n = int(evidence.get("n", 0) or 0)
        sqn = float(evidence.get("sqn", 0.0) or 0.0)
    except (TypeError, ValueError):
        return False, "evidence_invalid"
    min_n = int(cfg.get("ENGINE_A_TRADE_EVIDENCE_MIN_N", 30) or 30)
    min_sqn = float(cfg.get("ENGINE_A_TRADE_EVIDENCE_MIN_SQN", 2.0) or 2.0)
    if n < min_n:
        return False, f"evidence_n_lt_{min_n}"
    if sqn <= min_sqn:
        return False, f"evidence_sqn_lte_{min_sqn:g}"
    return True, key or "evidence"


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
        log.info(
            "[ENGINE_A_TRADE_GATE] consumed trade eligibility override %s=%s for %s",
            override_key,
            enabled,
            display or symbol or "instrument",
        )
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

    evidence_reason = None
    if enabled and _as_bool(cfg.get("ENGINE_A_TRADE_EVIDENCE_REQUIRED", False), False):
        qualified, evidence_reason = _evidence_qualified(
            cfg,
            [display, symbol, score_group, asset_type, "default"],
        )
        if not qualified:
            enabled = False
            source = f"{source}:{evidence_reason}"

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


def _is_engine_b_only_execution_signal(sig: dict | None) -> bool:
    """True for naked / B-only structural execution payloads (not Engine A gate scope)."""
    sig = sig or {}
    if sig.get("is_naked"):
        return True
    eng = str(sig.get("engine") or sig.get("source_engine") or "").strip().lower()
    if eng in ("engine_b", "naked", "naked_structure", "structure", "smc", "b"):
        return True
    verdict = str(sig.get("verdict") or "").strip().upper()
    if verdict in ("B_ONLY", "B_ONLY_SCORED", "B_ONLY_VISION_CONFIRMED"):
        return True
    engine_a_row = eng in ("a", "engine_a", "scalp", "scalp_vp", "engine_d")
    eb = sig.get("engine_b")
    if isinstance(eb, dict) and eb and not engine_a_row:
        return True
    if sig.get("naked_data") and not engine_a_row:
        return True
    return False


def _looks_like_engine_a_signal(sig: dict | None) -> bool:
    sig = sig or {}
    if _is_engine_b_only_execution_signal(sig):
        return False
    if sig.get("confluenceScore") is not None:
        return True
    eng = str(sig.get("engine") or sig.get("source_engine") or "").strip().lower()
    return eng in (
        "a",
        "engine_a",
        "engine_a_v2",
        "factor_scoring",
        "forex_scoring",
        "engine_c",
        "engine_c_consensus",
        "consensus",
        "c",
    )


def engine_a_trade_gate_block_reason(
    sig: dict | None,
    pair_obj: dict | None = None,
    *,
    config: dict | None = None,
) -> str | None:
    """Return a fail-closed block reason for Engine A execution, or None if allowed."""
    sig = sig or {}
    if _is_engine_b_only_execution_signal(sig):
        return None
    if not _looks_like_engine_a_signal(sig):
        return None

    cfg = config if isinstance(config, dict) else CONFIG
    trade_enabled = sig.get("engineATradeEnabled")
    if trade_enabled is False:
        detail = sig.get("engineATradeGate") or {}
        reason = detail.get("reason") or "Engine A research-only; trade eligibility disabled."
        return f"ENGINE_A_RESEARCH_ONLY: {reason}"

    # Always resolve the class/pair evidence map. A stamped
    # engineATradeEnabled=True (e.g. demo-unvalidated TRADE) must not skip
    # ENGINE_A_TRADE_ENABLED_BY_CLASS / evidence. When the master switch is
    # off, resolve_engine_a_trade_eligibility returns enabled=True (legacy).
    pair = pair_obj if isinstance(pair_obj, dict) else {}
    if not pair:
        pair = {
            "display": sig.get("display") or sig.get("pair"),
            "symbol": sig.get("symbol"),
            "type": sig.get("type"),
            "scoreGroup": sig.get("scoreGroup") or sig.get("score_group"),
        }
    detail = resolve_engine_a_trade_eligibility(pair, sig, config=cfg)
    if not detail.get("enabled"):
        return f"ENGINE_A_RESEARCH_ONLY: {detail.get('reason', 'Engine A research-only')}"
    return None


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


def apply_fail_closed_engine_a_trade_gate(
    signal: dict,
    *,
    source: str = "error_closed",
    reason: str | None = None,
) -> dict:
    """Mark a signal research-only when trade-eligibility annotation fails."""
    reason = reason or "Engine A trade-eligibility gate unavailable; research-only fail-closed."
    signal["engineATradeEnabled"] = False
    signal["engineATradeGate"] = {
        "enabled": False,
        "research_only": True,
        "source": source,
        "reason": reason,
    }
    signal["trade"] = False
    signal["executable"] = False
    warnings = signal.setdefault("warnings", [])
    if isinstance(warnings, list):
        warning = f"{_DISABLED_WARNING_PREFIX}: {reason}"
        if warning not in warnings:
            warnings.append(warning)
    return signal
