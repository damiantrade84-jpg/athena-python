"""Final advisory safety guard for AI Agent chat responses."""

from __future__ import annotations

import re
from typing import Any

from ai_contradiction_detector import detect_ai_contradictions


_UNSAFE_EXECUTION_RE = re.compile(
    r"\b(place|send|route|open|execute|submit)\s+(the\s+)?(trade|order|position)\b",
    re.IGNORECASE,
)

_VALID_SETUP_BLOCKING_CONTRADICTIONS = {
    "AI_VALID_SETUP_WITH_BLOCKED_DETERMINISTIC_GATES",
    "DATA_FRESHNESS_STALE_OR_UNKNOWN",
    "ENGINE_D_PROXY_FLOW_TREATED_AS_REAL",
    "FEE_GUARD_FAILED",
    "GUARDIAN_OR_KILL_SWITCH_NOT_CLEAN",
    "RR_BELOW_MIN",
    "RR_MISSING",
    "SCALP_SPREAD_MISSING",
}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return str(value or "").strip().upper()


def _float_or_none(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _packet_gate_failed(packet: dict[str, Any] | None) -> tuple[bool, list[str]]:
    packet = packet if isinstance(packet, dict) else {}
    flags: list[str] = []
    if not packet:
        return False, flags

    deterministic = _as_dict(packet.get("deterministic_gates"))
    engine_d = _as_dict(packet.get("engine_d"))
    risk = _as_dict(packet.get("risk"))
    data_quality = _as_dict(packet.get("data_quality"))

    for key in ("trade", "advisory_rule_trade_allowed", "execution_allowed", "risk_allowed", "freshness_allowed"):
        if packet.get(key) is False or deterministic.get(key) is False:
            flags.append(f"{key}_false")

    if engine_d.get("executable") is False:
        flags.append("engine_d_executable_false")
    if engine_d.get("rr_ok") is False:
        flags.append("engine_d_rr_ok_false")
    if engine_d.get("strict_fabio_pass") is False:
        flags.append("strict_fabio_pass_false")

    rr = _float_or_none(risk.get("rr") if risk.get("rr") is not None else engine_d.get("rr1"))
    min_rr = _float_or_none(risk.get("min_rr"))
    if rr is not None and min_rr is not None and rr < min_rr:
        flags.append("rr_below_min")

    fee_guard = engine_d.get("fee_guard") or risk.get("fees")
    if isinstance(fee_guard, dict) and (
        fee_guard.get("engine_d_reject_reason") or fee_guard.get("reject_reason") or fee_guard.get("reason")
    ):
        flags.append("fee_guard_failed")

    guardian = _text(risk.get("guardian_status"))
    kill = _text(risk.get("kill_switch_status"))
    if guardian and guardian not in {"CLEAN", "OK", "PASS", "NORMAL", "NONE"}:
        flags.append("guardian_not_clean")
    if kill and kill not in {"CLEAN", "OK", "PASS", "OFF", "INACTIVE", "NONE"}:
        flags.append("kill_switch_not_clean")

    freshness = _text(data_quality.get("freshness_status") or risk.get("data_freshness_status"))
    if freshness and any(token in freshness for token in ("STALE", "UNKNOWN", "MISSING", "DELAY", "EXPIRED", "UNAVAILABLE", "FALSE")):
        flags.append("data_freshness_not_clean")

    return bool(flags), flags


def validate_ai_chat_response(response: dict, packet: dict | None = None) -> dict[str, Any]:
    """Enforce the Phase 3 advisory-only safety envelope.

    This function may downgrade unsafe advisory language or decisions, but it
    never upgrades a response.
    """
    safe = dict(response or {})
    safety = dict(safe.get("safety") or {})
    safety.update(
        {
            "read_only": True,
            "can_execute": False,
            "can_modify_thresholds": False,
            "deterministic_gates_required": True,
        }
    )
    safe["safety"] = safety
    safe["ai_may_execute"] = False
    safe["can_execute"] = False

    safety_flags = list(safe.get("safety_flags") or [])
    packet_failed, packet_flags = _packet_gate_failed(packet)
    safety_flags.extend(flag for flag in packet_flags if flag not in safety_flags)

    try:
        contradictions = detect_ai_contradictions(packet or {}, safe)
        for flag in contradictions.get("flags") or []:
            if flag not in safe.get("contradiction_flags", []):
                safe.setdefault("contradiction_flags", []).append(flag)
            if flag not in safety_flags:
                safety_flags.append(flag)
        if contradictions.get("severity") in {"critical", "high"}:
            packet_failed = True
        if any(flag in _VALID_SETUP_BLOCKING_CONTRADICTIONS for flag in contradictions.get("flags") or []):
            packet_failed = True
    except Exception:
        pass

    final_action = str(safe.get("final_action") or "")
    answer = str(safe.get("answer") or "")
    if _UNSAFE_EXECUTION_RE.search(final_action) or _UNSAFE_EXECUTION_RE.search(answer):
        safety_flags.append("unsupported_execution_language_downgraded")
        safe["final_action"] = "require_user_review_advisory_only"

    if safe.get("decision") == "VALID_SETUP" and packet_failed:
        safe["decision"] = "BLOCKED_BY_RISK"
        safe["final_action"] = "blocked_by_deterministic_or_risk_gate"
        risk_warning = safe.get("risk_warning")
        warning = "AI cannot upgrade a setup while deterministic, freshness, guardian, kill-switch, RR, fee, spread, or risk gates are not clean."
        safe["risk_warning"] = f"{risk_warning} {warning}".strip() if risk_warning else warning

    if safe.get("decision") == "VALID_SETUP":
        safe["final_action"] = safe.get("final_action") or "advisory_valid_setup_requires_external_gates"

    safe["safety_flags"] = sorted(set(str(flag) for flag in safety_flags if flag))
    return safe
