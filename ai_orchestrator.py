"""Safe AI trade-review orchestration scaffold.

Phase 1 is advisory-only. These helpers compose packet context, historical
evidence scaffolding, and deterministic contradiction checks. They do not call
broker/execution paths, mutate thresholds, or authorize trades.
"""

from __future__ import annotations

from typing import Any

from ai_context import build_ai_review_packet
from ai_contracts import AIReviewPacket, AITradeDecision
from ai_contradiction_detector import detect_ai_contradictions
from ai_similar_setups import find_similar_setups


def _config_bool(key: str, default: bool) -> bool:
    try:
        from config import CONFIG as _CONFIG

        return bool(_CONFIG.get(key, default))
    except BaseException:
        return default


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _missing_sections(packet: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    for section in ("engine_a", "engine_b", "engine_c", "engine_d", "vision", "risk", "data_quality"):
        fields = _as_dict(packet.get(section)).get("missing_fields") or []
        for field in fields:
            missing.append(f"{section}.{field}")
    return missing


def _gate_blocked(packet: dict[str, Any]) -> bool:
    engine_d = _as_dict(packet.get("engine_d"))
    deterministic = _as_dict(packet.get("deterministic_gates"))
    gate = str(engine_d.get("gate_result") or "").upper()
    status = str(engine_d.get("candidate_status") or "").upper()
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
        value = str(packet.get(key) or deterministic.get(key) or "").upper()
        if any(token in value for token in ("BLOCK", "FAIL", "REJECT", "NO_TRADE", "SKIP", "WATCHLIST", "WAIT")):
            return True
    if engine_d.get("executable") is False:
        return True
    if engine_d.get("rr_ok") is False or engine_d.get("strict_fabio_pass") is False:
        return True
    if gate and gate not in {"PASS", "PASSED", "VALID", "CLEAR", "OK", "EXECUTABLE"}:
        return True
    if any(token in status for token in ("BLOCK", "FAIL", "REJECT", "RR_BELOW", "FEE", "WATCHLIST", "WAIT")):
        return True
    return False


def _fresh_enough(packet: dict[str, Any]) -> bool:
    dq = _as_dict(packet.get("data_quality"))
    risk = _as_dict(packet.get("risk"))
    freshness = str(dq.get("freshness_status") or risk.get("data_freshness_status") or "").upper()
    if not freshness:
        return False
    return not any(token in freshness for token in ("STALE", "UNKNOWN", "MISSING", "DELAY", "EXPIRED", "UNAVAILABLE", "FALSE", "BLOCK"))


def _similar_summary(similar: dict[str, Any]) -> str:
    n = int(similar.get("aggregate_sample_size") or 0)
    reliability = similar.get("reliability") or "unavailable"
    if n < 20:
        return f"Historical scaffold reliability is {reliability}; sample size {n} is not enough for calibrated probability."
    wr = similar.get("win_rate")
    avg_r = similar.get("avg_r")
    return f"Historical scaffold reliability is {reliability}; sample size {n}, win_rate={wr}, avg_r={avg_r}."


_RISK_BLOCKING_FLAGS = {
    "RR_BELOW_MIN",
    "FEE_GUARD_FAILED",
    "GUARDIAN_OR_KILL_SWITCH_NOT_CLEAN",
    "AI_VALID_SETUP_WITH_BLOCKED_DETERMINISTIC_GATES",
}

_SAFETY_DATA_FLAGS = {
    "RR_MISSING",
    "SCALP_SPREAD_MISSING",
    "DATA_FRESHNESS_STALE_OR_UNKNOWN",
}


def build_packet_for_signal(
    signal: dict,
    user_question: str | None = None,
    vision: dict | None = None,
) -> dict[str, Any]:
    """Build a canonical packet and attach read-only similar-setup evidence."""
    signal = signal if isinstance(signal, dict) else {}
    packet = build_ai_review_packet(signal, user_question=user_question, vision=vision)
    if "audit_db" in signal and "audit_db" not in packet:
        packet["audit_db"] = signal["audit_db"]
    if "_audit_db" in signal and "_audit_db" not in packet:
        packet["_audit_db"] = signal["_audit_db"]

    if signal.get("similar_outcomes"):
        packet["similar_outcomes"] = signal["similar_outcomes"]
    elif _config_bool("AI_SIMILAR_SETUPS_ENABLED", True):
        try:
            packet["similar_outcomes"] = find_similar_setups(packet)
        except Exception as exc:
            packet["similar_outcomes"] = {
                "examples": [],
                "aggregate_sample_size": 0,
                "win_rate": None,
                "avg_r": None,
                "profit_factor": None,
                "oos_decay": None,
                "reliability": "unavailable",
                "warning": f"Similar-setup lookup failed: {exc}",
                "missing_fields": [],
            }

    AIReviewPacket.model_validate(packet)
    return packet


def run_ai_trade_review(packet: dict, mode: str = "review") -> dict[str, Any]:
    """Return an AITradeDecision-compatible deterministic review scaffold."""
    packet = packet if isinstance(packet, dict) else {}
    missing = _missing_sections(packet)
    similar = _as_dict(packet.get("similar_outcomes"))
    contradictions = detect_ai_contradictions(packet)
    gate_blocked = _gate_blocked(packet)
    freshness_ok = _fresh_enough(packet)
    risk = _as_dict(packet.get("risk"))
    engine_d = _as_dict(packet.get("engine_d"))
    market_intelligence = _as_dict(packet.get("market_intelligence"))
    vision = _as_dict(packet.get("vision"))
    vision_read = _as_dict(vision.get("structured_trade_read"))
    supports: list[str] = []
    confirmation_needed: list[str] = []
    risk_notes: list[str] = []

    if packet.get("direction"):
        supports.append(f"Direction context present: {packet.get('direction')}")
    if risk.get("rr") is not None or engine_d.get("rr1") is not None:
        supports.append(f"RR context present: {risk.get('rr') if risk.get('rr') is not None else engine_d.get('rr1')}")
    else:
        confirmation_needed.append("Risk:reward must be available before this can be treated as a complete setup.")
    if not freshness_ok:
        confirmation_needed.append("Fresh non-stale market data is required.")
    if market_intelligence.get("freshness_status") in {"unavailable", "stale"}:
        confirmation_needed.append("Market intelligence is unavailable or stale; do not infer macro confirmation.")
    if vision_read and vision_read.get("allowed_for_execution_context") is False:
        confirmation_needed.append("Vision is stale, timestamp-missing, or UI-only; use as advisory context only.")
    if gate_blocked:
        risk_notes.append("Deterministic Engine D or risk gate is not clean; AI cannot upgrade it.")
    contradiction_flags = contradictions.get("flags") or []
    for flag in contradiction_flags:
        if flag in _RISK_BLOCKING_FLAGS:
            risk_notes.append(flag)

    reliability = similar.get("reliability") or "unavailable"
    sample_size = int(similar.get("aggregate_sample_size") or 0)
    confidence_type = "calibrated_from_outcomes" if sample_size >= 20 and reliability in {"weak", "moderate", "strong"} else "insufficient_sample"
    if confidence_type == "calibrated_from_outcomes":
        confidence = max(0.05, min(0.95, float(similar.get("win_rate") or 0.5)))
    else:
        confidence = 0.35 if missing else 0.5

    risk_blocked = gate_blocked or any(flag in contradiction_flags for flag in _RISK_BLOCKING_FLAGS)
    safety_data_missing = any(flag in contradiction_flags for flag in _SAFETY_DATA_FLAGS)
    if risk_blocked or contradictions.get("severity") in {"critical", "high"}:
        decision = "BLOCKED_BY_RISK" if risk_blocked else "WAIT_FOR_CONFIRMATION"
    elif safety_data_missing:
        decision = "DATA_INSUFFICIENT"
    elif missing and (not freshness_ok or len(missing) >= 8):
        decision = "DATA_INSUFFICIENT"
    elif not missing and freshness_ok:
        decision = "VALID_SETUP"
    else:
        decision = "WATCHLIST"

    if decision == "VALID_SETUP":
        confirmation_needed.append("Deterministic execution gates, risk gates, freshness, RR, spread, and fee checks still must pass outside AI.")

    out = AITradeDecision(
        decision=decision,
        confidence=confidence,
        confidence_type=confidence_type,
        thesis="Advisory trade review built from deterministic ATHENA context.",
        market_read=f"{packet.get('symbol') or 'Selected setup'} {packet.get('direction') or 'direction unknown'} review.",
        supports=supports,
        contradictions=contradictions.get("flags") or [],
        confirmation_needed=confirmation_needed,
        invalidation="Use deterministic SL/invalidation from the signal; unavailable if missing from packet.",
        risk_notes=risk_notes,
        historical_evidence_summary=_similar_summary(similar),
        final_action="advisory_review_only",
        ai_may_execute=False,
        deterministic_gates_required=True,
        mode=mode,
        contradiction_flags=contradictions.get("flags") or [],
        context_missing_fields=missing,
        market_intelligence={
            "freshness_status": market_intelligence.get("freshness_status", "unavailable"),
            "warnings": market_intelligence.get("warnings") or [],
        },
        vision_summary={
            "right_edge_status": vision_read.get("right_edge_status") or vision.get("right_edge_status"),
            "tf_alignment": vision_read.get("tf_alignment") or vision.get("tf_alignment"),
            "freshness_status": vision_read.get("freshness_status") or vision.get("freshness_status"),
            "allowed_for_execution_context": bool(vision_read.get("allowed_for_execution_context", False)),
        },
    ).model_dump(mode="json")
    out["contradiction_flags"] = contradictions.get("flags") or []
    out["contradictions_count"] = contradictions.get("contradictions_count", 0)
    out["severity"] = contradictions.get("severity")
    return out


def summarize_trade_for_chat(packet: dict) -> dict[str, Any]:
    """Summarize packet facts for a read-only chat response."""
    packet = packet if isinstance(packet, dict) else {}
    missing = _missing_sections(packet)
    facts = [
        f"symbol={packet.get('symbol') or 'unknown'}",
        f"direction={packet.get('direction') or 'unknown'}",
        f"style={packet.get('requested_style') or 'unknown'}",
        f"engine_source={packet.get('engine_source') or 'unknown'}",
    ]
    risk = _as_dict(packet.get("risk"))
    engine_d = _as_dict(packet.get("engine_d"))
    if risk.get("rr") is not None or engine_d.get("rr1") is not None:
        facts.append(f"rr={risk.get('rr') if risk.get('rr') is not None else engine_d.get('rr1')}")
    if engine_d.get("gate_result") is not None:
        facts.append(f"engine_d_gate={engine_d.get('gate_result')}")
    if _as_dict(packet.get("data_quality")).get("freshness_status") is not None:
        facts.append(f"freshness={_as_dict(packet.get('data_quality')).get('freshness_status')}")
    market_intelligence = _as_dict(packet.get("market_intelligence"))
    if market_intelligence:
        facts.append(f"market_intelligence={market_intelligence.get('freshness_status', 'unavailable')}")
    vision = _as_dict(packet.get("vision"))
    vision_read = _as_dict(vision.get("structured_trade_read"))
    if vision_read:
        facts.append(f"vision={vision_read.get('right_edge_status', 'UNKNOWN')} freshness={vision_read.get('freshness_status', 'unknown')}")
    return {
        "facts_used": facts,
        "missing_data": missing,
        "market_intelligence": {
            "freshness_status": market_intelligence.get("freshness_status", "unavailable"),
            "warnings": market_intelligence.get("warnings") or [],
            "risk_regime": _as_dict(market_intelligence.get("macro_regime")).get("risk_regime", "unknown"),
            "calendar_within_72h": _as_dict(market_intelligence.get("macro_regime")).get("calendar_within_72h") or [],
            "source_status": market_intelligence.get("source_status") or {},
        },
        "vision_summary": {
            "right_edge_status": vision_read.get("right_edge_status") or vision.get("right_edge_status"),
            "tf_alignment": vision_read.get("tf_alignment") or vision.get("tf_alignment"),
            "freshness_status": vision_read.get("freshness_status") or vision.get("freshness_status") or "unknown",
            "allowed_for_execution_context": bool(vision_read.get("allowed_for_execution_context", False)),
            "style_ratings": vision_read.get("style_ratings") or vision.get("style_ratings") or {},
            "visible_obstacles": _as_dict(vision_read.get("visible_structure")).get("obstacles_before_tp") or vision.get("visible_obstacles") or [],
            "memo": vision_read.get("memo"),
        },
    }
