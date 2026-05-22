"""Deterministic scalp AI review summary."""

from __future__ import annotations

from typing import Any

_HUMAN_ACTION_MAP = {
    "take": "trade",
    "wait": "wait",
    "reject": "reject",
    "needs_fresher_data": "watch",
    "needs_better_rr": "watch",
}

_POOR_ENTRY_PATTERNS = (
    "poor entry",
    "bad entry",
    "late entry",
    "chasing",
    "extended",
    "at vah",
    "at val",
    "into resistance",
    "into support",
)


def _clamp(score: float) -> int:
    return int(max(0, min(100, round(score))))


def _model_score(model_summary: dict[str, Any] | None, key: str, fallback: int) -> int:
    if not model_summary:
        return fallback
    raw = model_summary.get(key)
    if raw is None:
        return fallback
    try:
        return _clamp(float(raw))
    except (TypeError, ValueError):
        return fallback


def _map_human_action(raw: str) -> str:
    action = str(raw or "wait").strip().lower()
    return _HUMAN_ACTION_MAP.get(action, "watch")


def _score_visual(ai_review: dict[str, Any]) -> int:
    if ai_review.get("visual_contradiction"):
        return 22
    confirmation = str(ai_review.get("visual_confirmation") or "").lower()
    if any(w in confirmation for w in ("strong", "clear", "confirm", "aligned")):
        return 85
    if confirmation:
        return 65
    return 45


def _score_entry(ai_review: dict[str, Any]) -> int:
    text = str(ai_review.get("entry_quality") or "").lower()
    score = 72
    for pattern in _POOR_ENTRY_PATTERNS:
        if pattern in text:
            score -= 16
    if not text.strip():
        score = 50
    return _clamp(score)


def _score_source_quality(engine_d_ctx: dict[str, Any], ai_review: dict[str, Any]) -> int:
    source = engine_d_ctx.get("sourceContract") or {}
    score = 78.0
    if source.get("strictOrderflowSourcePass") is False:
        score -= 28
    if source.get("strictTimestampAlignmentPass") is False:
        score -= 12
    unavailable = source.get("unavailableReasons") or []
    if isinstance(unavailable, list):
        score -= min(24, len(unavailable) * 6)
    assess = str(ai_review.get("source_quality_assessment") or "").lower()
    if any(w in assess for w in ("proxy", "unverified", "insufficient", "blocked")):
        score -= 18
    return _clamp(score)


def _score_risk(ai_review: dict[str, Any], mismatch_warnings: list[str] | None) -> int:
    score = 80.0
    risks = ai_review.get("risks") or []
    if isinstance(risks, list):
        score -= min(30, len(risks) * 8)
    if mismatch_warnings:
        score -= min(15, len(mismatch_warnings) * 4)
    return _clamp(score)


def _score_tradeability(
    visual: int,
    entry: int,
    source_quality: int,
    ai_review: dict[str, Any],
) -> int:
    base = min(visual, entry, source_quality)
    if ai_review.get("visual_contradiction"):
        base -= 24
    return _clamp(base)


def _overall(tradeability: int, visual: int, entry: int, risk: int, source_quality: int) -> int:
    blended = (
        tradeability * 0.30
        + visual * 0.20
        + entry * 0.15
        + risk * 0.15
        + source_quality * 0.20
    )
    return _clamp(blended)


def build_scalp_ai_review_summary(
    engine_d_ctx: dict[str, Any],
    ai_review: dict[str, Any],
    provider_meta: dict[str, Any],
    *,
    mismatch_warnings: list[str] | None = None,
    model_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ms = model_summary if isinstance(model_summary, dict) else {}
    setup = engine_d_ctx.get("scalpSetup") or {}
    visual = _model_score(ms, "visualConfirmationScore", _score_visual(ai_review))
    entry = _model_score(ms, "entryQualityScore", _score_entry(ai_review))
    source_quality = _model_score(
        ms, "sourceQualityScore", _score_source_quality(engine_d_ctx, ai_review)
    )
    risk = _model_score(ms, "riskScore", _score_risk(ai_review, mismatch_warnings))
    tradeability = _model_score(
        ms,
        "tradeabilityScore",
        _score_tradeability(visual, entry, source_quality, ai_review),
    )
    overall = _model_score(
        ms, "overallScore", _overall(tradeability, visual, entry, risk, source_quality)
    )
    try:
        confidence = int(ms.get("confidence", ai_review.get("confidence", 0)))
    except (TypeError, ValueError):
        confidence = 0
    confidence = max(0, min(100, confidence))

    human_raw = ms.get("humanAction") or ai_review.get("human_action") or "wait"
    reasons = ai_review.get("supporting_reasons") or []
    final_reason = str(ms.get("finalReason") or "").strip()
    if not final_reason and isinstance(reasons, list) and reasons:
        final_reason = str(reasons[0])[:500]

    return {
        "provider": provider_meta.get("provider"),
        "model": provider_meta.get("model"),
        "providerStatus": str(provider_meta.get("provider_status") or "unknown"),
        "fallbackUsed": bool(provider_meta.get("fallback_used")),
        "humanAction": _map_human_action(str(human_raw)),
        "setupType": ms.get("setupType") or setup.get("setupType") or engine_d_ctx.get("signal", {}).get("zone_type"),
        "overallScore": overall,
        "tradeabilityScore": tradeability,
        "visualConfirmationScore": visual,
        "entryQualityScore": entry,
        "riskScore": risk,
        "sourceQualityScore": source_quality,
        "confidence": confidence,
        "finalReason": final_reason or "Awaiting clearer scalp setup context",
        "engineD": {
            "aiGrade": engine_d_ctx.get("ai_grade"),
            "aiScore": engine_d_ctx.get("ai_score"),
            "direction": engine_d_ctx.get("direction"),
            "executable": engine_d_ctx.get("executable"),
            "executionTf": engine_d_ctx.get("execution_tf"),
        },
    }
