"""Lee/Hermes advisory confirmation card engine.

This module is intentionally read-only. Lee may provide a structured advisory
read, but Athena deterministic gates remain authoritative and can only downgrade
or fail closed.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

from ai_contracts import LeeConfirmationResponse, LeeExternalContext, LeeReasoningDraft
from ai_orchestrator import build_packet_for_signal

log = logging.getLogger("athena")

LEE_LABELS = {
    "CONTEXT_SUPPORTS": "Context supports",
    "WAIT": "Wait / needs confirmation",
    "CONTEXT_BLOCKS": "Lee flags risk",
    "NEED_MORE_DATA": "Data gap",
}

_UNSAFE_EXECUTION_RE = re.compile(
    r"("
    r"\b(place|send|route|open|execute|submit|approve|authorize)\s+(the\s+)?(trade|order|position|entry)\b"
    r"|\b(buy|sell)\s+(now|immediately)\b"
    r"|\b(go|enter)\s+(long|short)\b"
    r"|\btake\s+(the\s+)?trade\b"
    r"|\b(confirmed|approved)\s+(entry|trade|order)\b"
    r"|\b(entry|trade|order)\s+(confirmed|approved)\b"
    r"|\b(market|limit)\s+order\b"
    r")",
    re.IGNORECASE,
)

_GATE_BLOCKING_FLAGS = {
    "trade_false",
    "advisory_rule_trade_allowed_false",
    "execution_allowed_false",
    "risk_allowed_false",
    "freshness_allowed_false",
    "engine_d_executable_false",
    "engine_d_rr_ok_false",
    "strict_fabio_pass_false",
    "rr_below_min",
    "fee_guard_failed",
    "guardian_not_clean",
    "kill_switch_not_clean",
    "data_freshness_not_clean",
}

_BLOCKING_TEXT_TOKENS = (
    "BLOCK",
    "BLOCKED",
    "REJECT",
    "REJECTED",
    "NO_TRADE",
    "FAIL",
    "FAILED",
    "FALSE",
    "DENY",
    "DENIED",
    "KILL",
    "STALE",
    "EXPIRED",
    "UNAVAILABLE",
)

_BLOCKING_TEXT_KEYS = (
    "gate_result",
    "candidate_status",
    "final_state",
    "finalState",
    "state",
    "status",
    "block_reason",
    "reject_reason",
    "reason",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item is not None and str(item).strip()]


def _append_unique(items: list[str], *values: str) -> list[str]:
    for value in values:
        text = str(value or "").strip()
        if text and text not in items:
            items.append(text)
    return items


def _text(value: Any) -> str:
    return str(value or "").strip().upper()


def _float_or_none(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _deterministic_block_flags(packet: dict[str, Any]) -> list[str]:
    packet = packet if isinstance(packet, dict) else {}
    flags: list[str] = []
    deterministic = _as_dict(packet.get("deterministic_gates"))
    engine_d = _as_dict(packet.get("engine_d"))
    risk = _as_dict(packet.get("risk"))
    data_quality = _as_dict(packet.get("data_quality"))

    for key in ("trade", "advisory_rule_trade_allowed", "execution_allowed", "risk_allowed", "freshness_allowed"):
        if packet.get(key) is False or deterministic.get(key) is False:
            flags.append(f"{key}_false")

    for source_name, source in (
        ("packet", packet),
        ("deterministic", deterministic),
        ("engine_d", engine_d),
        ("risk", risk),
        ("data_quality", data_quality),
    ):
        for key in _BLOCKING_TEXT_KEYS:
            value = _text(source.get(key))
            if value and any(token in value for token in _BLOCKING_TEXT_TOKENS):
                flags.append(f"{source_name}_{key}_blocking")

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

    try:
        from ai_contradiction_detector import detect_ai_contradictions

        contradictions = detect_ai_contradictions(packet)
        for flag in contradictions.get("flags") or []:
            if flag in {
                "AI_VALID_SETUP_WITH_BLOCKED_DETERMINISTIC_GATES",
                "DATA_FRESHNESS_STALE_OR_UNKNOWN",
                "ENGINE_D_PROXY_FLOW_TREATED_AS_REAL",
                "FEE_GUARD_FAILED",
                "GUARDIAN_OR_KILL_SWITCH_NOT_CLEAN",
                "RR_BELOW_MIN",
                "RR_MISSING",
                "SCALP_SPREAD_MISSING",
            }:
                flags.append(str(flag))
        if contradictions.get("severity") in {"critical", "high"}:
            flags.append("contradiction_severity_block")
    except Exception:
        pass

    return sorted(set(flags))


def _has_value(*sources: dict[str, Any], key: str) -> bool:
    return any(isinstance(source, dict) and source.get(key) is not None for source in sources)


def _deterministic_missing_fields(packet: dict[str, Any], signal: dict[str, Any]) -> list[str]:
    """Return deterministic gate evidence missing from both packet and signal.

    Lee may only call context supportive when Athena's authoritative gate facts are
    present. Missing evidence is not treated as clean.
    """

    packet = packet if isinstance(packet, dict) else {}
    signal = signal if isinstance(signal, dict) else {}
    deterministic = _as_dict(packet.get("deterministic_gates"))
    packet_engine_d = _as_dict(packet.get("engine_d"))
    signal_engine_d = _as_dict(signal.get("engine_d"))
    packet_risk = _as_dict(packet.get("risk"))
    signal_risk = _as_dict(signal.get("risk"))
    packet_quality = _as_dict(packet.get("data_quality"))
    data_freshness = _as_dict(signal.get("dataFreshness"))

    missing: list[str] = []
    for key in ("trade", "execution_allowed", "risk_allowed", "freshness_allowed"):
        if not _has_value(packet, deterministic, signal, key=key):
            missing.append(key)

    if not _has_value(packet_engine_d, signal_engine_d, key="executable"):
        missing.append("engine_d.executable")
    if not _has_value(packet_engine_d, signal_engine_d, key="rr_ok"):
        missing.append("engine_d.rr_ok")
    if not _has_value(packet_risk, signal_risk, key="guardian_status"):
        missing.append("risk.guardian_status")
    if not _has_value(packet_risk, signal_risk, key="kill_switch_status"):
        missing.append("risk.kill_switch_status")
    if not (
        _has_value(packet_risk, signal_risk, key="data_freshness_status")
        or _has_value(packet_quality, key="freshness_status")
        or _has_value(data_freshness, key="allowed")
    ):
        missing.append("data_freshness")

    return sorted(set(missing))


def _selected_signal_summary(signal: dict[str, Any], packet: dict[str, Any] | None = None) -> dict[str, Any] | None:
    src: dict[str, Any] = {}
    if isinstance(signal, dict):
        src.update(signal)
    if isinstance(packet, dict):
        for key, value in packet.items():
            src.setdefault(key, value)
    if not src:
        return None

    def pick(*keys: str) -> Any:
        for key in keys:
            if key in src and src[key] is not None:
                return src[key]
        return None

    summary = {
        "symbol": pick("symbol", "pair", "display"),
        "trace_id": pick("trace_id", "id"),
        "direction": pick("direction", "side"),
        "engine": pick("engine", "engine_source", "source_engine", "source"),
        "state": pick("state", "candidate_status", "finalState", "gate_result"),
        "score": pick("score", "conviction", "confidence"),
        "threshold": pick("threshold", "score_threshold"),
        "rr": pick("rr", "rr1", "rRatio", "r_ratio"),
        "entry": pick("entry", "entry_price"),
        "sl": pick("sl", "stop", "stop_loss"),
        "tp": pick("tp", "take_profit", "tp1"),
        "style": pick("style", "requested_style", "timeframe_style"),
    }
    if not any(value is not None for value in summary.values()):
        return None
    return summary


def _build_external_context(packet: dict[str, Any]) -> LeeExternalContext:
    market_intelligence = _as_dict(packet.get("market_intelligence"))
    vision = _as_dict(packet.get("vision"))
    return LeeExternalContext(
        market_intelligence_freshness=str(market_intelligence.get("freshness_status") or "unavailable"),
        vision_freshness=vision.get("freshness_status"),
        source_status=_as_dict(market_intelligence.get("source_status")),
        warnings=_string_list(market_intelligence.get("warnings")),
        missing_fields=_string_list(market_intelligence.get("missing_fields")) + _string_list(vision.get("missing_fields")),
    )


def _build_packet(signal: dict[str, Any], request: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    warnings: list[str] = []
    try:
        return build_packet_for_signal(signal, user_question="Lee advisory confirmation check"), warnings
    except Exception as exc:
        warnings.append(f"lee_packet_build_warning: {exc}")
        try:
            from ai_context import build_ai_review_packet

            return build_ai_review_packet(signal, user_question="Lee advisory confirmation check"), warnings
        except Exception as fallback_exc:
            warnings.append(f"lee_packet_build_failed: {fallback_exc}")
            symbol = request.get("symbol") or signal.get("symbol") or signal.get("pair")
            return {
                "schema_version": "1.0",
                "trace_id": str(request.get("trace_id") or signal.get("trace_id") or ""),
                "symbol": symbol,
                "direction": signal.get("direction"),
                "market_intelligence": _as_dict(signal.get("market_intelligence")),
                "deterministic_gates": {},
                "risk": _as_dict(signal.get("risk")),
                "engine_d": _as_dict(signal.get("engine_d")),
                "data_quality": {},
            }, warnings


class HermesLeeReasoningAdapter:
    """Controlled optional LLM adapter for Lee's narrative reasoning.

    The adapter returns a structured draft only. The finalizer below owns the
    displayed verdict and safety envelope. If config/client/model is unavailable,
    it returns None so the deterministic fallback can keep the route local and
    fail-safe.
    """

    def draft(self, evidence_packet: dict[str, Any]) -> dict[str, Any] | None:
        try:
            from config import CONFIG, ai_key_configured

            # External Lee/Hermes reasoning is opt-in at runtime; deterministic
            # fallback keeps the endpoint fast and fail-closed when the app has
            # not explicitly enabled or wired a reasoning adapter yet.
            if not bool(CONFIG.get("AI_LEE_CONFIRMATION_LLM_ENABLED", False)):
                return None
            if not ai_key_configured(CONFIG):
                return None

            from config import AITemperatureConfig, create_ai_client, get_ai_model, get_ai_timeout_sec

            model = get_ai_model(CONFIG, preferred_key="LEE_MODEL")
            client = create_ai_client(CONFIG)
            completion = client.chat.completions.create(
                model=model,
                max_tokens=500,
                temperature=float(AITemperatureConfig.get_temperature("marcus")),
                timeout=get_ai_timeout_sec(CONFIG),
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are Lee inside Athena. Return JSON only. You are read-only advisory context. "
                            "Never authorize, place, route, submit, or execute trades. Athena deterministic gates are authoritative."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            "Return strict JSON with keys lee_verdict, confidence, narrative, supports, risks, missing_data. "
                            "Allowed lee_verdict values: CONTEXT_SUPPORTS, WAIT, CONTEXT_BLOCKS, NEED_MORE_DATA. "
                            "Evidence packet:\n" + json.dumps(evidence_packet, default=str)[:8000]
                        ),
                    },
                ],
            )
            text = (completion.choices[0].message.content or "").strip()
            if not text:
                return None
            return json.loads(text)
        except Exception as exc:
            log.warning("[LEE_CONFIRMATION] Lee reasoning adapter failed after being enabled; failing closed: %s", exc)
            return LeeReasoningDraft(
                lee_verdict="NEED_MORE_DATA",
                confidence="low",
                narrative="Lee reasoning was unavailable or malformed, so Athena failed closed.",
                risks=["lee_reasoning_adapter_failed"],
                missing_data=["valid_lee_reasoning"],
                model_used="hermes_adapter_failed",
            ).model_dump(mode="json")


def _call_adapter(adapter: Any, evidence_packet: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str], list[str]]:
    flags: list[str] = []
    warnings: list[str] = []
    explicit_adapter = adapter is not None
    active_adapter = adapter or HermesLeeReasoningAdapter()
    try:
        draft = active_adapter.draft(evidence_packet) if callable(getattr(active_adapter, "draft", None)) else active_adapter(evidence_packet)
    except Exception as exc:
        if explicit_adapter:
            flags.append("lee_reasoning_invalid")
        warnings.append(f"Lee reasoning adapter failed: {exc}")
        return None, flags, warnings

    if draft is None:
        if explicit_adapter:
            flags.append("lee_reasoning_invalid")
            warnings.append("Lee reasoning adapter returned no structured output.")
        return None, flags, warnings
    if isinstance(draft, str):
        try:
            draft = json.loads(draft)
        except Exception:
            flags.append("lee_reasoning_invalid")
            warnings.append("Lee reasoning adapter returned invalid JSON.")
            return None, flags, warnings
    if not isinstance(draft, dict):
        flags.append("lee_reasoning_invalid")
        warnings.append("Lee reasoning adapter returned a non-object response.")
        return None, flags, warnings
    try:
        return LeeReasoningDraft.model_validate(draft).model_dump(mode="json"), flags, warnings
    except Exception as exc:
        flags.append("lee_reasoning_invalid")
        warnings.append(f"Lee reasoning adapter schema invalid: {exc}")
        return None, flags, warnings


def _deterministic_draft(packet: dict[str, Any], gate_flags: list[str]) -> dict[str, Any]:
    market_intelligence = _as_dict(packet.get("market_intelligence"))
    mi_status = str(market_intelligence.get("freshness_status") or "unavailable")
    direction = packet.get("direction") or "direction unknown"
    symbol = packet.get("symbol") or "the selected setup"

    if gate_flags:
        return LeeReasoningDraft(
            lee_verdict="CONTEXT_BLOCKS",
            confidence="medium",
            narrative="Lee flags this as blocked or incomplete because Athena deterministic gates/risk checks are not clean.",
            risks=["; ".join(gate_flags)],
            missing_data=[],
            model_used="deterministic_fallback",
        ).model_dump(mode="json")

    if mi_status in {"fresh", "partial"}:
        return LeeReasoningDraft(
            lee_verdict="CONTEXT_SUPPORTS",
            confidence="medium" if mi_status == "fresh" else "low",
            narrative=f"Lee's advisory read: external context currently supports monitoring {symbol} {direction}, but this is not execution approval.",
            supports=[f"market_intelligence={mi_status}", "server-verified Athena signal context"],
            risks=_string_list(market_intelligence.get("warnings")),
            missing_data=[],
            model_used="deterministic_fallback",
        ).model_dump(mode="json")

    return LeeReasoningDraft(
        lee_verdict="NEED_MORE_DATA",
        confidence="low",
        narrative="Lee needs fresh external context before making a trade-specific advisory read.",
        supports=[],
        risks=[f"market_intelligence={mi_status}"],
        missing_data=["fresh_market_intelligence"],
        model_used="deterministic_fallback",
    ).model_dump(mode="json")


def _sanitize_narrative(narrative: str, safety_flags: list[str]) -> str:
    text = str(narrative or "").strip()
    if _UNSAFE_EXECUTION_RE.search(text):
        _append_unique(safety_flags, "unsafe_adapter_language_downgraded")
        return "Lee's advisory wording was downgraded because execution language is not allowed. Athena deterministic gates remain authoritative."
    return text or "Lee needs more verified Athena context before making an advisory read."


def run_lee_confirmation(
    request: dict[str, Any],
    signal: dict[str, Any] | None,
    *,
    server_verified_signal: bool,
    adapter: Any | None = None,
    context_resolution: dict[str, Any] | None = None,
    pre_safety_flags: list[str] | None = None,
    pre_warnings: list[str] | None = None,
) -> dict[str, Any]:
    request = request if isinstance(request, dict) else {}
    signal = signal if isinstance(signal, dict) else {}
    safety_flags = list(pre_safety_flags or [])
    warnings = list(pre_warnings or [])
    context_resolution = dict(context_resolution or {})
    context_resolution.setdefault("server_verified_signal", bool(server_verified_signal))

    if not server_verified_signal:
        _append_unique(safety_flags, "server_signal_not_verified")
        missing = ["server_verified_signal"]
        if not signal:
            missing.append("server_signal")
        response = LeeConfirmationResponse(
            generated_at=_now(),
            trace_id=request.get("trace_id") or signal.get("trace_id"),
            symbol=request.get("symbol") or signal.get("symbol") or signal.get("pair"),
            lee_verdict="NEED_MORE_DATA",
            display_label=LEE_LABELS["NEED_MORE_DATA"],
            confidence="low",
            narrative="Lee needs a server-verified Athena signal before giving trade-specific advisory support.",
            missing_data=missing,
            warnings=warnings,
            safety_flags=sorted(set(safety_flags)),
            selected_signal=_selected_signal_summary(signal),
            context_resolution=context_resolution,
            trade_specific_confirmation_allowed=False,
        )
        return response.model_dump(mode="json")

    packet, packet_warnings = _build_packet(signal, request)
    warnings.extend(packet_warnings)
    gate_flags = _deterministic_block_flags(packet)
    missing_gate_fields = _deterministic_missing_fields(packet, signal)
    if gate_flags:
        _append_unique(safety_flags, "deterministic_gate_block", *gate_flags)
    if missing_gate_fields:
        _append_unique(safety_flags, "deterministic_gate_evidence_incomplete")
        warnings.append("Lee confirmation missing authoritative deterministic gate evidence; failing closed.")

    evidence_packet = {
        "packet": packet,
        "server_verified_signal": True,
        "safety_flags": safety_flags,
        "external_context": _build_external_context(packet).model_dump(mode="json"),
    }
    draft, adapter_flags, adapter_warnings = _call_adapter(adapter, evidence_packet)
    _append_unique(safety_flags, *adapter_flags)
    warnings.extend(adapter_warnings)

    if draft is None:
        if "lee_reasoning_invalid" in safety_flags:
            draft = LeeReasoningDraft(
                lee_verdict="NEED_MORE_DATA",
                confidence="low",
                narrative="Lee reasoning returned invalid structured output, so Athena failed closed.",
                missing_data=["valid_lee_reasoning"],
                model_used="invalid_lee_reasoning_fallback",
            ).model_dump(mode="json")
        else:
            draft = _deterministic_draft(packet, gate_flags)

    verdict = str(draft.get("lee_verdict") or "NEED_MORE_DATA")
    if gate_flags and verdict == "CONTEXT_SUPPORTS":
        verdict = "CONTEXT_BLOCKS"
    if "lee_reasoning_invalid" in safety_flags:
        verdict = "NEED_MORE_DATA"
    if verdict not in LEE_LABELS:
        _append_unique(safety_flags, "lee_verdict_invalid_downgraded")
        verdict = "NEED_MORE_DATA"

    missing_data = _string_list(draft.get("missing_data"))
    if missing_gate_fields:
        verdict = "NEED_MORE_DATA"
        _append_unique(missing_data, *(f"deterministic_gate:{field}" for field in missing_gate_fields))
    if verdict == "NEED_MORE_DATA" and not missing_data and "lee_reasoning_invalid" in safety_flags:
        missing_data.append("valid_lee_reasoning")

    narrative = _sanitize_narrative(str(draft.get("narrative") or ""), safety_flags)
    if "unsafe_adapter_language_downgraded" in safety_flags and not gate_flags:
        verdict = "NEED_MORE_DATA"
        _append_unique(missing_data, "safe_lee_reasoning")
    if gate_flags and verdict == "CONTEXT_BLOCKS" and "deterministic gates" not in narrative.lower():
        narrative = f"{narrative} Athena deterministic gate flags remain blocking: {', '.join(gate_flags[:8])}."

    trade_specific_allowed = bool(
        server_verified_signal
        and verdict == "CONTEXT_SUPPORTS"
        and not gate_flags
        and not missing_gate_fields
        and "lee_reasoning_invalid" not in safety_flags
        and "unsafe_adapter_language_downgraded" not in safety_flags
    )
    external_context = _build_external_context(packet)
    market_intelligence = _as_dict(packet.get("market_intelligence"))

    response = LeeConfirmationResponse(
        generated_at=_now(),
        trace_id=packet.get("trace_id") or request.get("trace_id") or signal.get("trace_id"),
        symbol=packet.get("symbol") or request.get("symbol") or signal.get("symbol") or signal.get("pair"),
        lee_verdict=verdict,
        display_label=LEE_LABELS[verdict],
        confidence=draft.get("confidence") if draft.get("confidence") in {"low", "medium", "high"} else "low",
        narrative=narrative,
        supports=_string_list(draft.get("supports")),
        risks=_string_list(draft.get("risks")) + gate_flags,
        missing_data=missing_data,
        warnings=warnings,
        safety_flags=sorted(set(safety_flags)),
        market_intelligence=market_intelligence,
        external_context=external_context,
        selected_signal=_selected_signal_summary(signal, packet),
        context_resolution=context_resolution,
        model_used=str(draft.get("model_used") or "deterministic_fallback"),
        trade_specific_confirmation_allowed=trade_specific_allowed,
    )
    return response.model_dump(mode="json")
