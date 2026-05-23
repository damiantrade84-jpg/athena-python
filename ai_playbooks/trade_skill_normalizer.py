"""Normalize and fail-closed validate AI trade-skill structured output."""

from __future__ import annotations

from typing import Any, Literal

from ai_playbooks.contracts import (
    TRADE_SKILL_VERSION,
    _BLOCKING_DECISIONS,
    _VALID_DECISIONS,
    _VALID_DIRECTIONS,
    _VALID_ENTRY_MODELS,
    extended_output_fields_for,
    required_output_fields_for,
)

ReviewType = Literal["engine_a_chart", "engine_d_scalp"]

_ENGINE_D_REQUIRED = frozenset(
    {
        "marketState",
        "locationAssessment",
        "aggressionAssessment",
        "entryModel",
        "decision",
    }
)


def _coerce_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _coerce_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _coerce_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        v = float(value)
        if v != v or abs(v) == float("inf"):
            return None
        return v
    if isinstance(value, str) and value.strip():
        try:
            v = float(value.strip())
        except ValueError:
            return None
        if v != v or abs(v) == float("inf"):
            return None
        return v
    return None


def _coerce_int(value: Any, default: int = 0) -> int:
    f = _coerce_float(value)
    if f is None:
        return default
    return max(0, min(100, int(f)))


def _coerce_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        low = value.strip().lower()
        if low in ("true", "1", "yes"):
            return True
        if low in ("false", "0", "no"):
            return False
    return None


def _pick_trade_skill(parsed: dict[str, Any]) -> dict[str, Any]:
    nested = parsed.get("tradeSkill")
    if isinstance(nested, dict):
        return nested
    return parsed


def _map_decision_to_legacy(decision: str, entry_allowed: bool) -> tuple[str, str]:
    """Return (human_action, verdict)."""
    d = decision.upper()
    if d == "ENTRY_NOW" and entry_allowed:
        return "take", "VALID"
    if d in ("NO_TRADE", "INVALIDATED"):
        return "reject", "NO_TRADE" if d == "NO_TRADE" else "INVALID"
    if d == "WATCH_ONLY":
        return "needs_fresher_data", "CAUTION"
    if d in ("WAIT_FOR_PULLBACK", "WAIT_FOR_ACCEPTANCE"):
        return "wait", "CAUTION"
    return "wait", "CAUTION"


def _infer_decision_from_legacy(parsed: dict[str, Any]) -> str | None:
    decision = _coerce_str(parsed.get("decision")).upper()
    if decision in _VALID_DECISIONS:
        return decision
    human = _coerce_str(parsed.get("human_action")).lower()
    verdict = _coerce_str(parsed.get("verdict")).upper()
    summary = parsed.get("aiReviewSummary") or parsed.get("ai_review_summary") or {}
    if isinstance(summary, dict) and summary.get("humanAction"):
        human = str(summary["humanAction"]).lower()
    if human == "take" or verdict == "VALID":
        return "ENTRY_NOW"
    if human == "reject" or verdict in ("INVALID", "NO_TRADE"):
        return "NO_TRADE"
    if human in ("needs_fresher_data", "needs_better_rr"):
        return "WATCH_ONLY"
    if human == "wait" or verdict == "CAUTION":
        return "WAIT_FOR_PULLBACK"
    return None


def _passthrough_dict(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict) and value:
        return dict(value)
    return None


def _attach_engine_d_extended_fields(out: dict[str, Any], src: dict[str, Any]) -> None:
    """Passthrough advisory Fabio/Carmine fields without fail-closed gates."""
    agg_class = _coerce_str(src.get("aggressionClassification") or src.get("aggression_classification"))
    if agg_class:
        out["aggressionClassification"] = agg_class.upper()
    effort = _coerce_str(
        src.get("effortVsResultClassification") or src.get("effort_vs_result_classification")
    )
    if effort:
        out["effortVsResultClassification"] = effort.upper()
    for key, snake in (
        ("trappedTraderAssessment", "trapped_trader_assessment"),
        ("targetLogic", "target_logic"),
        ("invalidationAssessment", "invalidation_assessment"),
        ("managementPlan", "management_plan"),
    ):
        nested = _passthrough_dict(src.get(key) or src.get(snake))
        if nested:
            out[key] = nested
    session_quality = _coerce_str(src.get("sessionQuality") or src.get("session_quality"))
    if session_quality:
        out["sessionQuality"] = session_quality.upper()
    session_adj = _coerce_str(
        src.get("sessionConvictionAdjustment") or src.get("session_conviction_adjustment")
    )
    if session_adj:
        out["sessionConvictionAdjustment"] = session_adj.upper()


def normalize_trade_skill_output(
    parsed: dict[str, Any],
    *,
    review_type: ReviewType,
    backend_levels: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Return sanitized trade-skill dict and warning codes."""
    warnings: list[str] = []
    src = _pick_trade_skill(parsed)

    decision = _coerce_str(src.get("decision")).upper()
    source_decision = decision
    if decision not in _VALID_DECISIONS:
        inferred = _infer_decision_from_legacy(parsed)
        decision = inferred or "WATCH_ONLY"
        if inferred is None:
            warnings.append("invalid_decision")

    direction = _coerce_str(src.get("direction")).upper()
    if direction not in _VALID_DIRECTIONS:
        direction = "NEUTRAL"

    confidence = _coerce_int(src.get("confidence"), 0)
    market_state = _coerce_str(src.get("marketState") or src.get("market_state"))
    location = _coerce_str(src.get("locationAssessment") or src.get("location_assessment"))
    aggression = _coerce_str(src.get("aggressionAssessment") or src.get("aggression_assessment"))
    entry_model = _coerce_str(src.get("entryModel") or src.get("entry_model")).upper()
    if entry_model and entry_model not in _VALID_ENTRY_MODELS:
        entry_model = ""
    invalidation_level = _coerce_float(src.get("invalidationLevel") or src.get("invalidation_level"))
    invalidation_reason = _coerce_str(src.get("invalidationReason") or src.get("invalidation_reason"))
    wait_reason = _coerce_str(src.get("waitReason") or src.get("wait_reason"))
    no_trade_reason = _coerce_str(src.get("noTradeReason") or src.get("no_trade_reason"))
    chart_summary = _coerce_str(src.get("chartReadSummary") or src.get("chart_read_summary"))
    required_confirmation = _coerce_list(
        src.get("requiredConfirmation") or src.get("required_confirmation")
    )
    risk_notes = _coerce_list(src.get("riskNotes") or src.get("risk_notes"))

    entry_allowed = _coerce_bool(src.get("entryAllowedNow") if "entryAllowedNow" in src else src.get("entry_allowed_now"))
    if entry_allowed is None:
        entry_allowed = decision == "ENTRY_NOW"

    downgraded_no_trade_without_hard_reason = False

    # Engine D ENTRY_NOW always requires the required scalp review fields.
    if review_type == "engine_d_scalp":
        missing_d = []
        if not market_state:
            missing_d.append("marketState")
        if not location:
            missing_d.append("locationAssessment")
        if not aggression:
            missing_d.append("aggressionAssessment")
        if not entry_model:
            missing_d.append("entryModel")
        if missing_d and decision == "ENTRY_NOW":
            warnings.append("engine_d_required_fields_missing")
            decision = "WATCH_ONLY"
            entry_allowed = False
            if not no_trade_reason and not wait_reason:
                wait_reason = "Required Engine D fields missing: " + ", ".join(missing_d)

    # Blocking decisions always disable entry
    if decision in _BLOCKING_DECISIONS:
        entry_allowed = False

    source_declared_no_trade = (
        source_decision == "NO_TRADE"
        or _coerce_str(src.get("verdict") or parsed.get("verdict")).upper() == "NO_TRADE"
    )
    hard_no_trade_reason = (
        bool(no_trade_reason)
        or invalidation_level is not None
        or bool(invalidation_reason)
    )
    if decision == "NO_TRADE" and source_declared_no_trade and not hard_no_trade_reason:
        decision = "WATCH_ONLY"
        entry_allowed = False
        downgraded_no_trade_without_hard_reason = True
        warnings.append("no_trade_without_hard_reason_downgraded")
        if not wait_reason:
            wait_reason = "NO_TRADE requires a hard invalidation or concrete noTradeReason"

    # Direction must be LONG/SHORT for ENTRY_NOW
    if decision == "ENTRY_NOW" and direction not in ("LONG", "SHORT"):
        entry_allowed = False
        decision = "WATCH_ONLY"
        warnings.append("direction_missing_for_entry")

    # Engine D ENTRY_NOW requires invalidation
    if review_type == "engine_d_scalp" and decision == "ENTRY_NOW":
        if invalidation_level is None and backend_levels:
            sl = _coerce_float(backend_levels.get("stop_loss") or backend_levels.get("stopLoss"))
            if sl is not None:
                invalidation_level = sl
        if invalidation_level is None:
            decision = "WATCH_ONLY"
            entry_allowed = False
            warnings.append("invalidation_missing")
            if not wait_reason:
                wait_reason = "Invalidation level required for ENTRY_NOW"

    # ENTRY_NOW with missing required fields -> WATCH_ONLY
    if decision == "ENTRY_NOW" and "engine_d_required_fields_missing" in warnings:
        decision = "WATCH_ONLY"
        entry_allowed = False

    # Final fail-closed: only ENTRY_NOW + entry_allowed true stays entry
    if decision != "ENTRY_NOW":
        entry_allowed = False

    human_action, verdict = _map_decision_to_legacy(decision, entry_allowed)
    # Preserve explicit legacy fields only when they still agree with the final
    # fail-closed decision after all downgrades.
    legacy_human = _coerce_str(parsed.get("human_action")).lower()
    legacy_verdict = _coerce_str(parsed.get("verdict")).upper()
    mapped_human, mapped_verdict = _map_decision_to_legacy(decision, entry_allowed)
    if (
        not downgraded_no_trade_without_hard_reason
        and legacy_human in {"take", "wait", "reject", "needs_fresher_data", "needs_better_rr"}
        and legacy_human == mapped_human
    ):
        human_action = legacy_human
    if (
        not downgraded_no_trade_without_hard_reason
        and legacy_verdict in {"VALID", "CAUTION", "INVALID", "NO_TRADE"}
        and legacy_verdict == mapped_verdict
    ):
        verdict = legacy_verdict

    out: dict[str, Any] = {
        "tradeSkillVersion": TRADE_SKILL_VERSION,
        "reviewType": review_type,
        "decision": decision,
        "direction": direction,
        "confidence": confidence,
        "entryAllowedNow": entry_allowed,
        "chartReadSummary": chart_summary,
        "requiredConfirmation": required_confirmation,
        "riskNotes": risk_notes,
        "tradeSkillWarnings": warnings,
        "human_action": human_action,
        "verdict": verdict,
    }
    if market_state:
        out["marketState"] = market_state
    if location:
        out["locationAssessment"] = location
    if aggression:
        out["aggressionAssessment"] = aggression
    if entry_model:
        out["entryModel"] = entry_model
    if invalidation_level is not None:
        out["invalidationLevel"] = invalidation_level
    if invalidation_reason:
        out["invalidationReason"] = invalidation_reason
    if wait_reason:
        out["waitReason"] = wait_reason
    if no_trade_reason:
        out["noTradeReason"] = no_trade_reason

    if review_type == "engine_d_scalp":
        _attach_engine_d_extended_fields(out, src)

    return out, warnings


def trade_skill_parse_failure(review_type: ReviewType) -> dict[str, Any]:
    """Fail-closed trade skill when JSON parse fails."""
    out, _ = normalize_trade_skill_output(
        {
            "tradeSkillVersion": TRADE_SKILL_VERSION,
            "reviewType": review_type,
            "decision": "WATCH_ONLY",
            "direction": "NEUTRAL",
            "confidence": 0,
            "entryAllowedNow": False,
            "human_action": "wait",
            "verdict": "CAUTION",
            "waitReason": "AI response JSON parse failed",
            "chartReadSummary": "",
        },
        review_type=review_type,
    )
    out["human_action"] = "wait"
    out["verdict"] = "CAUTION"
    return out


def render_trade_skill_prompt_schema(review_type: ReviewType) -> str:
    """Compact schema block for prompt injection."""
    fields = required_output_fields_for(review_type)
    lines = [
        "tradeSkillVersion: athena_trade_skill.v1",
        f"reviewType: {review_type}",
        "decision: ENTRY_NOW | WAIT_FOR_PULLBACK | WAIT_FOR_ACCEPTANCE | WATCH_ONLY | NO_TRADE | INVALIDATED",
        "direction: LONG | SHORT | NEUTRAL",
        "confidence: 0-100 integer",
        "entryAllowedNow: boolean (advisory only; never execution permission)",
    ]
    if review_type == "engine_d_scalp":
        lines.extend(
            [
                "marketState: trending|balancing|expanding|compressing|choppy|no_trade|transition",
                "locationAssessment: string",
                "aggressionAssessment: string",
                "aggressionClassification: ABSORPTION|INITIATIVE_AGGRESSION|EXHAUSTION|NO_CLEAR_FLOW",
                "effortVsResultClassification: HIGH_EFFORT_NO_RESULT|HIGH_EFFORT_HIGH_RESULT|LOW_EFFORT_STALLED_RESULT|LOW_EFFORT_HIGH_RESULT|UNKNOWN",
                "entryModel: LVN_REJECTION_CONTINUATION|PULLBACK_TO_VALUE_REJECTION|SWEEP_AND_RECLAIM|BREAK_RETEST_HOLD|FAILED_BREAKOUT_REVERSAL|ABSORPTION_REVERSAL|MOMENTUM_CONTINUATION_AFTER_PULLBACK|NO_TRADE",
                "invalidationLevel: number|null",
                "invalidationReason: string",
                "invalidationAssessment: { structuralInvalidationLevel, proposedStopLevel, stopPlacementValid, stopProblem, expectedBehavior }",
                "trappedTraderAssessment: { trappedSide, trapTrigger, squeezeFuelScore, explanation }",
                "targetLogic: { primaryTargetType, primaryTargetPrice, targetJustification, structuralRR }",
                "sessionQuality: CLEAN_DELIVERY|ACCEPTABLE|CHOP_RISK|NO_TRADE_WINDOW",
                "sessionConvictionAdjustment: UPGRADE|NEUTRAL|DOWNGRADE|HARD_REJECT",
                "managementPlan (required when decision=ENTRY_NOW): { moveToBETrigger, scaleOutPlan, invalidationExit, maxTimeInTradeReasoning }",
                "requiredConfirmation: string[]",
            ]
        )
    lines.extend(
        [
            "waitReason: string (when not ENTRY_NOW)",
            "noTradeReason: string (when NO_TRADE or INVALIDATED)",
            "chartReadSummary: string",
            "requiredOutputFields: " + ", ".join(fields),
        ]
    )
    if review_type == "engine_d_scalp":
        ext = extended_output_fields_for(review_type)
        lines.append("extendedOutputFields (advisory): " + ", ".join(ext))
    return "\n".join(f"- {line}" for line in lines)
