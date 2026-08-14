"""Engine A vs chart vs AI verdict comparison block."""

from __future__ import annotations

import re
from typing import Any

from ai_review.engine_a_context import freshness_is_policy_ok
from ai_review.engine_snapshots import extract_engine_snapshots
from ai_review.summary import _HUMAN_ACTION_MAP, _POOR_ENTRY_PATTERNS

_FINAL_DECISIONS = frozenset({"trade", "wait", "reject", "watch"})

_FALSE_STALE_DOWNGRADE_MARKERS = (
    "h4/d1 stale",
    "h4 stale",
    "d1 stale",
    "stale h4",
    "stale d1",
    "htf stale",
    "data stale",
    "stale data",
    "candle stale",
    "stale candle",
)


def _filter_false_stale_downgrades(
    downgrade_reasons: list[str],
    engine_a_ctx: dict[str, Any],
) -> list[str]:
    if not downgrade_reasons or not freshness_is_policy_ok(engine_a_ctx):
        return downgrade_reasons
    filtered: list[str] = []
    for reason in downgrade_reasons:
        lower = str(reason or "").strip().lower()
        if lower and any(marker in lower for marker in _FALSE_STALE_DOWNGRADE_MARKERS):
            continue
        filtered.append(reason)
    return filtered


def _to_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in ("true", "yes", "1"):
        return True
    if text in ("false", "no", "0"):
        return False
    return None


def _coerce_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value if v is not None and str(v).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _map_final_decision(raw: str) -> str | None:
    action = str(raw or "").strip().lower()
    mapped = _HUMAN_ACTION_MAP.get(action, action)
    if mapped in _FINAL_DECISIONS:
        return mapped
    if action in _FINAL_DECISIONS:
        return action
    return None


def _text_blob(ai_review: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in (
        "visual_confirmation",
        "visual_contradiction",
        "engine_a_alignment",
        "entry_quality",
        "atr_rr_assessment",
    ):
        val = ai_review.get(key)
        if val:
            parts.append(str(val))
    for key in ("supporting_reasons", "risks"):
        items = ai_review.get(key)
        if isinstance(items, list):
            parts.extend(str(i) for i in items)
    return " ".join(parts).lower()


def _is_negative_visual_text(value: Any) -> bool:
    text = str(value or "").strip().lower()
    return text in ("", "none", "no", "false", "n/a", "na")


def _looks_directional_contradiction(text: str) -> bool:
    if not text:
        return False
    direction_markers = (
        "direction",
        "bias",
        "opposite",
        "against engine",
        "opposes",
        "bullish vs short",
        "bearish vs long",
        "short vs long",
        "long vs short",
    )
    if any(marker in text for marker in direction_markers):
        return True
    return False


def _infer_chart_confirms_direction(ai_review: dict[str, Any], engine_a_ctx: dict[str, Any]) -> bool | None:
    text = _text_blob(ai_review)
    visual_contradiction = ai_review.get("visual_contradiction")
    if not _is_negative_visual_text(visual_contradiction) and _looks_directional_contradiction(
        str(visual_contradiction).lower()
    ):
        return False
    if any(w in text for w in ("against engine", "opposes", "bearish vs long", "bullish vs short")):
        return False
    direction = str(engine_a_ctx.get("direction") or "NONE").upper()
    if direction not in ("LONG", "SHORT"):
        return None
    trend_ok = (direction == "LONG" and _has_word(text, ("uptrend",))) or (
        direction == "SHORT" and _has_word(text, ("downtrend",))
    )
    if any(w in text for w in ("confirm", "aligned", "supports", "agree", "direction ok")) or trend_ok:
        return True
    align = str(ai_review.get("engine_a_alignment") or "").lower()
    if any(w in align for w in ("aligned", "agree", "confirm", "supports")):
        return True
    if ai_review.get("visual_confirmation"):
        return True
    return None


def _infer_chart_contradicts_direction(ai_review: dict[str, Any]) -> bool | None:
    visual_contradiction = ai_review.get("visual_contradiction")
    if not _is_negative_visual_text(visual_contradiction) and _looks_directional_contradiction(
        str(visual_contradiction).lower()
    ):
        return True
    text = _text_blob(ai_review)
    if any(w in text for w in ("against engine", "opposes")):
        return True
    return None


# Whole-word timing signals not already covered by _POOR_ENTRY_PATTERNS.
# Matched on word boundaries so "late" does not hit "latest"/"correlated"
# and "extended" does not hit unrelated prose. "vwap" is NOT a poor-entry
# signal — a reference to VWAP says nothing about whether timing is bad.
_POOR_TIMING_WORDS = ("extended", "overextended", "exhausted", "exhaustion")
_GOOD_TIMING_WORDS = ("pullback", "retest", "acceptance", "reclaim")
_GOOD_TIMING_PHRASES = ("good entry", "clean entry", "acceptable entry")


def _has_word(text: str, words: tuple[str, ...]) -> bool:
    return bool(text) and any(re.search(rf"\b{w}\b", text) for w in words)


def _infer_entry_timing(ai_review: dict[str, Any]) -> tuple[bool | None, bool | None]:
    text = _text_blob(ai_review)
    has_poor = any(pattern in text for pattern in _POOR_ENTRY_PATTERNS) or _has_word(
        text, _POOR_TIMING_WORDS
    )
    has_good = _has_word(text, _GOOD_TIMING_WORDS) or any(
        phrase in text for phrase in _GOOD_TIMING_PHRASES
    )
    if has_good and not has_poor:
        return True, False
    if has_poor and not has_good:
        return False, True
    # No signal, or mixed/conflicting signals: stay indeterminate rather than
    # defaulting to "timing poor".
    return None, None


def _comparison_verdict(
    *,
    engine_a_provided: bool,
    chart_confirms: bool | None,
    chart_contradicts_dir: bool | None,
    chart_contradicts_timing: bool | None,
    engine_passed: bool | None,
    final_decision: str | None,
) -> str:
    if not engine_a_provided:
        return "engine_a_missing"
    if chart_contradicts_dir is True:
        return "engine_a_contradicted"
    if chart_confirms is True and chart_contradicts_timing is True:
        return "engine_a_direction_confirmed_entry_rejected"
    if chart_confirms is True and final_decision in ("wait", "watch"):
        return "engine_a_direction_confirmed_wait"
    if chart_confirms is True and engine_passed and final_decision in ("trade",):
        return "engine_a_confirmed"
    if chart_confirms is True and engine_passed:
        return "engine_a_confirmed"
    if chart_confirms is False:
        return "engine_a_contradicted"
    if chart_confirms is None and chart_contradicts_timing:
        return "mixed"
    return "unknown"


def build_engine_a_verdict_comparison(
    engine_a_ctx: dict[str, Any],
    ai_review: dict[str, Any],
    *,
    model_comparison: dict[str, Any] | None = None,
    engine_snapshots: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build comparison with immutable server-owned Engine A facts."""
    snapshots = engine_snapshots or engine_a_ctx.get("engine_snapshots")
    if snapshots is None:
        snapshots = extract_engine_snapshots({}, engine_a_ctx)
    ea = snapshots.get("engineA") or {}
    model = dict(model_comparison or {})

    direction = str(ea.get("direction") or engine_a_ctx.get("direction") or "NONE").upper()
    score = _to_float(ea.get("score") if ea.get("score") is not None else engine_a_ctx.get("confluence_score"))
    max_score = _to_float(ea.get("maxScore") if ea.get("maxScore") is not None else engine_a_ctx.get("max_score_override"))
    threshold = _to_float(ea.get("threshold") if ea.get("threshold") is not None else engine_a_ctx.get("threshold"))
    normalized = _to_float(ea.get("normalizedScore"))
    passed = _to_bool(ea.get("passed") if ea.get("passed") is not None else engine_a_ctx.get("passed"))
    engine_a_provided = direction in ("LONG", "SHORT") and score is not None
    bias_valid = direction in ("LONG", "SHORT") and (
        passed is True or (score is not None and threshold is not None)
    )

    chart_confirms = _to_bool(model.get("chartConfirmsEngineADirection"))
    if chart_confirms is None:
        chart_confirms = _infer_chart_confirms_direction(ai_review, engine_a_ctx)

    chart_contradicts_dir = _to_bool(model.get("chartContradictsEngineADirection"))
    if chart_contradicts_dir is None:
        chart_contradicts_dir = _infer_chart_contradicts_direction(ai_review)

    timing_confirms = _to_bool(model.get("chartConfirmsEntryTiming"))
    timing_contradicts = _to_bool(model.get("chartContradictsEntryTiming"))
    if timing_confirms is None and timing_contradicts is None:
        timing_confirms, timing_contradicts = _infer_entry_timing(ai_review)

    human_raw = str(ai_review.get("human_action") or "wait")
    final_decision = _map_final_decision(model.get("finalDecision"))
    if final_decision is None:
        final_decision = _map_final_decision(human_raw)

    ai_agrees = _to_bool(model.get("aiAgreesWithEngineA"))
    if ai_agrees is None:
        if chart_contradicts_dir is True:
            ai_agrees = False
        elif chart_confirms is True and final_decision == "trade" and passed:
            ai_agrees = True
        elif chart_confirms is True and final_decision in ("wait", "watch") and passed:
            ai_agrees = None
        else:
            ai_agrees = None

    ai_downgraded = _to_bool(model.get("aiDowngradedEngineA"))
    if ai_downgraded is None:
        ai_downgraded = bool(
            passed
            and (
                final_decision in ("wait", "watch", "reject")
                or timing_contradicts is True
                or chart_contradicts_dir is True
            )
        )

    # AI review is advisory and cannot promote a deterministic Engine A failure.
    ai_upgraded = False

    comparison = _comparison_verdict(
        engine_a_provided=bool(engine_a_provided),
        chart_confirms=chart_confirms,
        chart_contradicts_dir=chart_contradicts_dir,
        chart_contradicts_timing=timing_contradicts,
        engine_passed=passed,
        final_decision=final_decision,
    )

    downgrade_reasons = _coerce_str_list(model.get("downgradeReasons"))
    if not downgrade_reasons and ai_downgraded:
        if timing_contradicts:
            downgrade_reasons.append("Chart entry timing poor or extended")
        if chart_contradicts_dir:
            downgrade_reasons.append("Chart contradicts Engine A direction")
        if final_decision in ("wait", "watch") and passed:
            downgrade_reasons.append("Engine A passed but human action is wait/watch")
    downgrade_reasons = _filter_false_stale_downgrades(downgrade_reasons, engine_a_ctx)

    upgrade_reasons: list[str] = []

    final_reason = str(model.get("finalReason") or "").strip() or None
    if not final_reason:
        if comparison == "engine_a_direction_confirmed_entry_rejected":
            final_reason = (
                f"Engine A {direction} is directionally confirmed, but immediate entry is downgraded. "
                f"Final decision: {(final_decision or 'wait').upper()}."
            )
        elif comparison == "engine_a_direction_confirmed_wait":
            final_reason = (
                f"Engine A {direction} is directionally confirmed; wait for location/timing. "
                f"Final decision: {(final_decision or 'wait').upper()}."
            )
        elif comparison == "engine_a_confirmed":
            final_reason = f"Engine A {direction} confirmed by chart. Final decision: {(final_decision or 'trade').upper()}."
        elif comparison == "engine_a_contradicted":
            final_reason = f"Chart contradicts Engine A {direction}. Final decision: {(final_decision or 'reject').upper()}."
        elif comparison == "engine_a_missing":
            final_reason = "Engine A context insufficient for comparison"
        else:
            reasons = ai_review.get("supporting_reasons") or []
            if isinstance(reasons, list) and reasons:
                final_reason = str(reasons[0])[:500]

    active = ea.get("activeFactors")

    return {
        "engineAProvided": bool(engine_a_provided),
        "engineABiasValid": bias_valid,
        "engineAPassed": passed,
        "engineADirection": direction if direction != "NONE" else None,
        "engineAScore": score,
        "engineAMaxScore": max_score,
        "engineAThreshold": threshold,
        "engineANormalizedScore": normalized,
        "engineAActiveFactors": active if isinstance(active, list) else None,
        "chartConfirmsEngineADirection": chart_confirms,
        "chartContradictsEngineADirection": chart_contradicts_dir,
        "chartConfirmsEntryTiming": timing_confirms,
        "chartContradictsEntryTiming": timing_contradicts,
        "aiAgreesWithEngineA": ai_agrees,
        "aiDowngradedEngineA": bool(ai_downgraded),
        "aiUpgradedEngineA": bool(ai_upgraded),
        "comparisonVerdict": comparison,
        "downgradeReasons": downgrade_reasons,
        "upgradeReasons": upgrade_reasons,
        "finalDecision": final_decision,
        "finalReason": final_reason,
        "modelClaims": {
            key: model.get(key)
            for key in (
                "engineAProvided",
                "engineABiasValid",
                "engineAPassed",
                "engineADirection",
                "engineAScore",
                "engineAMaxScore",
                "engineAThreshold",
                "engineANormalizedScore",
                "engineAActiveFactors",
                "comparisonVerdict",
                "aiUpgradedEngineA",
                "upgradeReasons",
            )
            if model.get(key) is not None
        },
    }
