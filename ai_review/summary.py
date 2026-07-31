"""Deterministic AI chart review summary block (ai_review_summary)."""

from __future__ import annotations

import copy
from typing import Any

from ai_review.context_diagnostics import context_tradeability_penalty
from ai_review.engine_snapshots import extract_engine_snapshots

_HUMAN_ACTION_MAP = {
    "take": "trade",
    "wait": "wait",
    "reject": "reject",
    "needs_fresher_data": "watch",
    "needs_better_rr": "watch",
}

_POOR_ENTRY_PATTERNS = (
    "resistance cluster",
    "resistance-cluster",
    "into resistance",
    "poor entry",
    "bad entry",
    "late entry",
    "chasing",
    "displaced",
    "compression",
    "low atr",
    "tight range",
    "no room",
)

_BAD_RR_PATTERNS = ("poor rr", "bad rr", "low rr", "rr too", "risk reward", "risk/reward")

_CONCORDANCE_ALIGNMENT = {
    "agree": 88,
    "partial": 62,
    "disagree": 28,
    "unknown": 40,
}


def _clamp(score: float) -> int:
    return int(max(0, min(100, round(score))))


def _optional_model_score(model_summary: dict[str, Any], key: str) -> int | None:
    raw = model_summary.get(key)
    if raw is None:
        return None
    try:
        return _clamp(float(raw))
    except (TypeError, ValueError):
        return None


def _text_blob(*parts: Any) -> str:
    return " ".join(str(p or "") for p in parts).lower()


def _map_human_action(raw: str) -> str:
    action = str(raw or "wait").strip().lower()
    return _HUMAN_ACTION_MAP.get(action, "watch")


def _score_visual(ai_review: dict[str, Any]) -> int:
    contradiction = str(ai_review.get("visual_contradiction") or "").strip()
    confirmation = str(ai_review.get("visual_confirmation") or "").strip()
    if contradiction:
        return 22
    if confirmation:
        lower = confirmation.lower()
        if any(w in lower for w in ("strong", "clear", "aligned", "confirms", "confirm")):
            return 88
        if any(w in lower for w in ("weak", "mixed", "unclear", "partial")):
            return 55
        return 72
    return 45


def _score_entry(ai_review: dict[str, Any]) -> int:
    text = _text_blob(ai_review.get("entry_quality"))
    score = 72
    for pattern in _POOR_ENTRY_PATTERNS:
        if pattern in text:
            score -= 18
    if not text.strip():
        score = 50
    return _clamp(score)


def _score_risk(
    ai_review: dict[str, Any],
    engine_a_ctx: dict[str, Any],
    mismatch_warnings: list[str] | None,
) -> int:
    score = 82.0
    risks = ai_review.get("risks") or []
    if isinstance(risks, list):
        score -= min(35, len(risks) * 8)

    atr = engine_a_ctx.get("atr") or {}
    if str(atr.get("atr_freshness_status") or "").lower() == "stale":
        score -= 22
    elif str(atr.get("atr_freshness_status") or "").lower() == "unknown":
        score -= 10

    fresh = str(ai_review.get("freshness_assessment") or "").lower()
    if "stale" in fresh or "uncertain" in fresh or "unknown" in fresh:
        score -= 12

    if engine_a_ctx.get("provider_mismatch"):
        score -= 10

    if mismatch_warnings:
        score -= min(15, len(mismatch_warnings) * 5)

    return _clamp(score)


def _score_engine_alignment(
    engine_a_ctx: dict[str, Any],
    concordance: dict[str, Any],
    ai_review: dict[str, Any],
) -> int:
    base = _CONCORDANCE_ALIGNMENT.get(
        str(concordance.get("concordance") or "unknown"), 40
    )
    if engine_a_ctx.get("passed"):
        base = max(base, 68)
    align_text = str(ai_review.get("engine_a_alignment") or "").lower()
    if any(w in align_text for w in ("aligned", "agree", "confirm", "supports")):
        base = max(base, 78)
    if any(w in align_text for w in ("contradict", "conflict", "disagree", "against")):
        base = min(base, 35)
    direction = str(engine_a_ctx.get("direction") or "NONE").upper()
    if direction in ("LONG", "SHORT"):
        base = max(base, 55)
    return _clamp(base)


def _tradeability_penalties(
    ai_review: dict[str, Any],
    engine_a_ctx: dict[str, Any],
) -> float:
    penalty = 0.0
    if ai_review.get("visual_contradiction"):
        penalty += 28
    entry_text = _text_blob(ai_review.get("entry_quality"))
    for pattern in _POOR_ENTRY_PATTERNS:
        if pattern in entry_text:
            penalty += 14
    atr_rr = _text_blob(ai_review.get("atr_rr_assessment"))
    for pattern in _BAD_RR_PATTERNS:
        if pattern in atr_rr:
            penalty += 16
    geometry = engine_a_ctx.get("geometry") or {}
    rr = geometry.get("rr")
    if rr is not None:
        try:
            if float(rr) < 1.5:
                penalty += 18
            elif float(rr) < 2.0:
                penalty += 8
        except (TypeError, ValueError):
            pass
    atr = engine_a_ctx.get("atr") or {}
    if str(atr.get("atr_freshness_status") or "").lower() == "stale":
        penalty += 20
    if engine_a_ctx.get("provider_mismatch"):
        penalty += 12
    if not engine_a_ctx.get("chart_provider_hint") and not engine_a_ctx.get("engine_a_provider"):
        penalty += 8
    penalty += context_tradeability_penalty(engine_a_ctx, ai_review)
    risks = [str(r).lower() for r in (ai_review.get("risks") or [])]
    for risk in risks:
        if any(p in risk for p in _POOR_ENTRY_PATTERNS + _BAD_RR_PATTERNS):
            penalty += 6
    return penalty


def _score_tradeability(
    engine_alignment: int,
    visual: int,
    entry: int,
    ai_review: dict[str, Any],
    engine_a_ctx: dict[str, Any],
) -> int:
    base = min(engine_alignment, visual, entry)
    base -= _tradeability_penalties(ai_review, engine_a_ctx)
    return _clamp(base)


def _overall_score(
    tradeability: int,
    visual: int,
    entry: int,
    risk: int,
) -> int:
    blended = (
        tradeability * 0.35
        + visual * 0.25
        + entry * 0.20
        + risk * 0.20
    )
    return _clamp(blended)


def _final_reason(
    ai_review: dict[str, Any],
    concordance: dict[str, Any],
    human_action: str,
) -> str:
    reasons = ai_review.get("supporting_reasons") or []
    if isinstance(reasons, list) and reasons:
        return str(reasons[0])[:500]
    note = str(concordance.get("divergence_note") or "").strip()
    if note:
        return note[:500]
    if ai_review.get("visual_contradiction"):
        return "Visual chart contradicts engine setup"
    if human_action == "watch":
        return "Setup needs better timing or context before trade"
    if human_action == "reject":
        return "Review rejects trade under current conditions"
    if human_action == "trade":
        return "Visual and engine context support trade consideration"
    return "Awaiting clearer entry and risk context"


def _collect_missing_snapshot_fields(snapshots: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    for engine_key, fields in (
        ("engineB", ("score", "maxScore", "threshold", "passed", "direction")),
        ("engineC", ("score", "maxScore", "threshold", "decisionState", "direction")),
        ("engineD", ("score", "maxScore", "threshold", "setupType", "direction")),
    ):
        block = snapshots.get(engine_key) or {}
        for field in fields:
            if block.get(field) is None:
                missing.append(f"{engine_key}.{field}")
    ea = snapshots.get("engineA") or {}
    if ea.get("threshold") is None:
        missing.append("engineA.threshold")
    if ea.get("score") is None:
        missing.append("engineA.score")
    return missing


def build_ai_review_summary(
    engine_a_ctx: dict[str, Any],
    ai_review: dict[str, Any],
    concordance: dict[str, Any],
    provider_meta: dict[str, Any],
    *,
    signal: dict[str, Any] | None = None,
    engine_snapshots: dict[str, Any] | None = None,
    mismatch_warnings: list[str] | None = None,
    model_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build top-level ai_review_summary; augments missing_context on a copy only."""
    snapshots = engine_snapshots
    if snapshots is None:
        snapshots = extract_engine_snapshots(signal or {}, engine_a_ctx)

    ai_copy = copy.deepcopy(ai_review)
    extra_missing = _collect_missing_snapshot_fields(snapshots)
    existing = list(ai_copy.get("missing_context") or [])
    for path in extra_missing:
        if path not in existing:
            existing.append(path)
    ai_copy["missing_context"] = existing

    ms = model_summary if isinstance(model_summary, dict) else {}
    human_raw = ms.get("humanAction") or ai_review.get("human_action") or "wait"
    human_action = _map_human_action(str(human_raw))
    visual = _score_visual(ai_review)
    entry = _score_entry(ai_review)
    risk = _score_risk(ai_review, engine_a_ctx, mismatch_warnings)
    alignment = _score_engine_alignment(engine_a_ctx, concordance, ai_review)
    tradeability = _score_tradeability(alignment, visual, entry, ai_review, engine_a_ctx)
    overall = _overall_score(tradeability, visual, entry, risk)
    deterministic_scores = {
        "overallScore": overall,
        "tradeabilityScore": tradeability,
        "engineAlignmentScore": alignment,
        "visualConfirmationScore": visual,
        "entryQualityScore": entry,
        "riskScore": risk,
    }
    model_scores = {
        key: _optional_model_score(ms, key)
        for key in deterministic_scores
    }

    try:
        confidence = int(ms.get("confidence", ai_review.get("confidence", 0)))
    except (TypeError, ValueError):
        confidence = 0
    confidence = max(0, min(100, confidence))

    setup_type = str(ms.get("setupType") or ai_review.get("setup_type") or "").strip() or None
    engine_d = dict(snapshots.get("engineD") or {})
    if setup_type and engine_d.get("setupType") is None:
        engine_d = {**engine_d, "setupType": setup_type}

    return {
        "provider": provider_meta.get("provider") or None,
        "model": provider_meta.get("model") or None,
        "providerStatus": str(provider_meta.get("provider_status") or "unknown"),
        "fallbackUsed": bool(provider_meta.get("fallback_used")),
        "humanAction": human_action,
        "setupType": setup_type,
        "overallScore": overall,
        "tradeabilityScore": tradeability,
        "engineAlignmentScore": alignment,
        "visualConfirmationScore": visual,
        "entryQualityScore": entry,
        "riskScore": risk,
        "confidence": confidence,
        "modelConfidence": confidence,
        "confidenceCalibrated": False,
        "deterministicScores": deterministic_scores,
        "modelScores": model_scores,
        "finalReason": str(ms.get("finalReason") or "").strip()
        or _final_reason(ai_review, concordance, human_action),
        "engineA": snapshots.get("engineA"),
        "engineB": snapshots.get("engineB"),
        "engineC": snapshots.get("engineC"),
        "engineD": engine_d,
    }
