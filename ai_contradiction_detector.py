"""Deterministic contradiction checks for advisory AI trade reviews."""

from __future__ import annotations

from typing import Any


_BAD_VERDICT_TOKENS = ("REJECT", "BLOCK", "NO_TRADE", "FAIL", "INVALID", "OPPOSE", "BEARISH", "BULLISH")
_STALE_TOKENS = ("STALE", "UNKNOWN", "MISSING", "DELAY", "EXPIRED", "UNAVAILABLE", "FALSE")
_PASS_TOKENS = ("PASS", "PASSED", "VALID", "CLEAR", "CLEAN", "OK", "EXECUTABLE", "TRADE")
_BLOCK_TOKENS = ("BLOCK", "FAIL", "REJECT", "NO_TRADE", "WATCHLIST", "WAIT", "INVALID", "RR_BELOW", "FEE")


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return str(value or "").strip().upper()


def _direction_opposes(source: Any, target: Any) -> bool:
    src = _text(source)
    dst = _text(target)
    if not src or not dst:
        return False
    long_terms = ("LONG", "BUY", "BULL")
    short_terms = ("SHORT", "SELL", "BEAR")
    src_long = any(term in src for term in long_terms)
    src_short = any(term in src for term in short_terms)
    dst_long = any(term in dst for term in long_terms)
    dst_short = any(term in dst for term in short_terms)
    return (src_long and dst_short) or (src_short and dst_long)


def _float_or_none(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _fee_guard_failed(fee_guard: Any) -> bool:
    if isinstance(fee_guard, dict):
        reason = fee_guard.get("engine_d_reject_reason") or fee_guard.get("reject_reason") or fee_guard.get("reason")
        if reason:
            return True
        status = _text(fee_guard.get("status") or fee_guard.get("result"))
        return any(token in status for token in ("FAIL", "REJECT", "BLOCK"))
    return any(token in _text(fee_guard) for token in ("FAIL", "REJECT", "BLOCK", "HIGH_COST"))


def _gate_blocked(packet: dict[str, Any]) -> bool:
    engine_d = _as_dict(packet.get("engine_d"))
    risk = _as_dict(packet.get("risk"))
    deterministic = _as_dict(packet.get("deterministic_gates"))
    gate = _text(engine_d.get("gate_result") or packet.get("gate_result"))
    candidate = _text(engine_d.get("candidate_status") or packet.get("candidate_status"))
    executable = engine_d.get("executable", packet.get("executable"))
    rr_ok = engine_d.get("rr_ok", packet.get("rr_ok"))
    strict = engine_d.get("strict_fabio_pass", packet.get("strict_fabio_pass"))
    guardian = _text(risk.get("guardian_status") or packet.get("guardian_status"))
    kill_switch = _text(risk.get("kill_switch_status") or packet.get("kill_switch_status"))
    for key in (
        "trade",
        "advisory_rule_trade_allowed",
        "execution_allowed",
        "risk_allowed",
        "freshness_allowed",
    ):
        if packet.get(key) is False or deterministic.get(key) is False:
            return True
    for key in ("signal_class", "signal_tier", "final_state", "block_reason", "engine_c_decision_state"):
        value = _text(packet.get(key) or deterministic.get(key))
        if any(token in value for token in _BLOCK_TOKENS) or any(token in value for token in ("SKIP", "NO_TRADE")):
            return True
    if executable is False or rr_ok is False or strict is False:
        return True
    if gate and not any(token in gate for token in _PASS_TOKENS):
        return True
    if candidate and any(token in candidate for token in _BLOCK_TOKENS):
        return True
    if guardian and guardian not in ("CLEAN", "OK", "PASS", "NORMAL", "NONE"):
        return True
    if kill_switch and kill_switch not in ("CLEAN", "OK", "PASS", "OFF", "INACTIVE", "NONE"):
        return True
    return False


def _severity(flags: list[str]) -> str:
    if not flags:
        return "none"
    critical = {
        "AI_VALID_SETUP_WITH_BLOCKED_DETERMINISTIC_GATES",
        "GUARDIAN_OR_KILL_SWITCH_NOT_CLEAN",
    }
    high = {
        "FEE_GUARD_FAILED",
        "RR_BELOW_MIN",
        "DATA_FRESHNESS_STALE_OR_UNKNOWN",
        "ENGINE_D_PROXY_FLOW_TREATED_AS_REAL",
    }
    if any(flag in critical for flag in flags):
        return "critical"
    if any(flag in high for flag in flags):
        return "high"
    if len(flags) >= 2:
        return "medium"
    return "low"


def detect_ai_contradictions(packet: dict, decision: dict | None = None) -> dict[str, Any]:
    """Detect deterministic contradictions in AI review inputs/outputs."""
    packet = packet if isinstance(packet, dict) else {}
    decision = decision if isinstance(decision, dict) else {}
    flags: list[str] = []
    direction = packet.get("direction")
    engine_a = _as_dict(packet.get("engine_a"))
    engine_b = _as_dict(packet.get("engine_b"))
    engine_d = _as_dict(packet.get("engine_d"))
    vision = _as_dict(packet.get("vision"))
    risk = _as_dict(packet.get("risk"))
    data_quality = _as_dict(packet.get("data_quality"))

    if _direction_opposes(engine_a.get("direction"), direction):
        flags.append("ENGINE_A_DIRECTION_OPPOSES_TRADE_DIRECTION")

    b_verdict = _text(engine_b.get("verdict"))
    if _direction_opposes(engine_b.get("direction"), direction):
        flags.append("ENGINE_B_DIRECTION_OPPOSES_TRADE_DIRECTION")
    elif b_verdict and any(token in b_verdict for token in _BAD_VERDICT_TOKENS):
        if not any(token in b_verdict for token in _PASS_TOKENS):
            flags.append("ENGINE_B_VERDICT_OPPOSES_TRADE_DIRECTION")

    if vision.get("confirms_direction") is False:
        flags.append("VISION_DOES_NOT_CONFIRM_DIRECTION")

    rr = _float_or_none(risk.get("rr") if risk.get("rr") is not None else engine_d.get("rr1"))
    min_rr = _float_or_none(risk.get("min_rr") if risk.get("min_rr") is not None else packet.get("min_rr"))
    if rr is None:
        flags.append("RR_MISSING")
    elif min_rr is not None and rr < min_rr:
        flags.append("RR_BELOW_MIN")

    freshness = _text(
        data_quality.get("freshness_status")
        or risk.get("data_freshness_status")
        or packet.get("freshness_status")
    )
    if not freshness or any(token in freshness for token in _STALE_TOKENS):
        flags.append("DATA_FRESHNESS_STALE_OR_UNKNOWN")

    proxy_order_flow = any(
        engine_d.get(key) is True
        for key in ("vp_is_proxy", "cvd_is_proxy", "absorption_is_proxy", "aggression_source_is_proxy")
    ) or engine_d.get("aggression_uses_real_order_flow") is False
    confidence = _float_or_none(decision.get("confidence"))
    if proxy_order_flow and (_text(decision.get("decision")) == "VALID_SETUP" or (confidence is not None and confidence >= 0.75)):
        flags.append("ENGINE_D_PROXY_FLOW_TREATED_AS_REAL")

    if _fee_guard_failed(engine_d.get("fee_guard") or risk.get("fees")):
        flags.append("FEE_GUARD_FAILED")

    style = _text(packet.get("requested_style") or engine_b.get("style"))
    engine_source = _text(packet.get("engine_source"))
    if ("SCALP" in style or "ENGINE_D" in engine_source) and engine_d.get("spread_pips") is None and risk.get("spread") is None:
        flags.append("SCALP_SPREAD_MISSING")

    guardian = _text(risk.get("guardian_status"))
    kill_switch = _text(risk.get("kill_switch_status"))
    clean_guardian = guardian in ("", "CLEAN", "OK", "PASS", "NORMAL", "NONE")
    clean_kill = kill_switch in ("", "CLEAN", "OK", "PASS", "OFF", "INACTIVE", "NONE")
    if not clean_guardian or not clean_kill:
        flags.append("GUARDIAN_OR_KILL_SWITCH_NOT_CLEAN")

    if _text(decision.get("decision")) == "VALID_SETUP" and _gate_blocked(packet):
        flags.append("AI_VALID_SETUP_WITH_BLOCKED_DETERMINISTIC_GATES")

    return {
        "contradictions_count": len(flags),
        "flags": flags,
        "severity": _severity(flags),
    }
