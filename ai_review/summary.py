"""Deterministic AI chart review summary block (ai_review_summary)."""

from __future__ import annotations

import copy
from typing import Any

from ai_review.context_diagnostics import context_tradeability_penalty
from ai_review.engine_snapshots import extract_engine_snapshots
from ai_review.suggested_trade_plan import resolve_watch_reason
from ai_review.visual_text import (
    has_visual_contradiction_text,
    is_directional_visual_contradiction,
)

_HUMAN_ACTION_MAP = {
    "take": "trade",
    "wait": "wait",
    "reject": "reject",
    "needs_fresher_data": "watch",
    "needs_better_rr": "watch",
}

_POOR_ENTRY_PATTERNS = (
    "poor_to_fair",
    "poor-to-fair",
    "poor to fair",
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
    contradiction = ai_review.get("visual_contradiction")
    confirmation = str(ai_review.get("visual_confirmation") or "").strip()
    # Only hard-penalize directional chart-vs-engine conflict. Non-directional
    # caveats (chop, stale capture, thin PA) stay mid-band so tradeability is
    # not forced to 0 when structure still aligns.
    if is_directional_visual_contradiction(contradiction):
        return 22
    if has_visual_contradiction_text(contradiction):
        return 48
    if confirmation:
        lower = confirmation.lower()
        if any(w in lower for w in ("strong", "clear", "aligned", "confirms", "confirm")):
            return 88
        if any(w in lower for w in ("weak", "mixed", "unclear", "partial")):
            return 55
        return 72
    return 45


def _score_entry(
    ai_review: dict[str, Any],
    engine_a_ctx: dict[str, Any] | None = None,
) -> int:
    # Prefer geometry-based entry quality when engine context is available (P1-1).
    if isinstance(engine_a_ctx, dict) and engine_a_ctx:
        try:
            from ai_review.review_geometry import score_entry_quality

            return score_entry_quality(engine_a_ctx=engine_a_ctx, ai_review=ai_review)
        except Exception:
            pass
    text = _text_blob(ai_review.get("entry_quality"))
    score = 72
    for pattern in _POOR_ENTRY_PATTERNS:
        if pattern in text:
            score -= 18
    if not text.strip():
        score = 50
    # If context stamped a precomputed score, use it.
    if isinstance(engine_a_ctx, dict):
        pre = engine_a_ctx.get("entry_quality_score")
        try:
            if pre is not None:
                return _clamp(float(pre))
        except (TypeError, ValueError):
            pass
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
    primary_engine = str(engine_a_ctx.get("primary_engine") or "A").upper()
    alignment_key = "engine_b_alignment" if primary_engine == "B" else "engine_a_alignment"
    align_text = str(ai_review.get(alignment_key) or "").lower()
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
    contradiction = ai_review.get("visual_contradiction")
    if is_directional_visual_contradiction(contradiction):
        penalty += 28
    elif has_visual_contradiction_text(contradiction):
        penalty += 10
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
    if is_directional_visual_contradiction(ai_review.get("visual_contradiction")):
        return "Visual chart contradicts engine setup"
    if has_visual_contradiction_text(ai_review.get("visual_contradiction")):
        return "Visual caveats reduce confidence; direction not fully contradicted"
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
    # _score_entry already prefers the geometry scorer and passes ai_review, so
    # its result carries both geometry and the model's text cues. The stamped
    # entry_quality_score is the pre-provider pass (ai_review=None) and is only
    # a fallback — overriding with it here discarded the text-aware score (F4).
    entry = _score_entry(ai_review, engine_a_ctx)
    risk = _score_risk(ai_review, engine_a_ctx, mismatch_warnings)
    # Prefer live RR risk pressure when geometry says chase.
    rg = (engine_a_ctx or {}).get("risk_geometry") or {}
    try:
        rr_live = float(rg.get("rr_live_tp1") or rg.get("rr_live") or 0)
        if rr_live and rr_live < 1.0:
            risk = min(risk, 35)
        elif rr_live and rr_live < 1.5:
            risk = min(risk, 55)
    except (TypeError, ValueError):
        pass
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
    # P2-5: separate target setup type (plan) from observed geometry.
    target_setup_type = setup_type
    observed_setup_type = None
    zone = str((engine_a_ctx or {}).get("zone_status") or "").upper()
    disp = None
    try:
        from ai_review.review_geometry import displacement_atr

        geom = (engine_a_ctx or {}).get("geometry") or {}
        ema = ((engine_a_ctx or {}).get("ema_levels") or {}).get("ema21")
        atr_v = ((engine_a_ctx or {}).get("atr") or {}).get("atr_value")
        disp = displacement_atr(
            geom.get("current_price") or geom.get("live_price"),
            ema,
            atr_v,
        )
    except Exception:
        disp = None
    if zone in {"ABOVE_ZONE", "BELOW_ZONE"} or (disp is not None and abs(disp) > 1.5):
        observed_setup_type = "EXTENDED_CONTINUATION"
    elif zone == "IN_ZONE":
        observed_setup_type = "AT_STRUCTURE"
    engine_d = dict(snapshots.get("engineD") or {})
    if setup_type and engine_d.get("setupType") is None:
        engine_d = {**engine_d, "setupType": setup_type}

    watch_state = (engine_a_ctx or {}).get("watch_state")
    if isinstance(watch_state, dict) and watch_state.get("state") == "WATCH":
        human_action = "wait"

    return {
        "provider": provider_meta.get("provider") or None,
        "model": provider_meta.get("model") or None,
        "providerStatus": str(provider_meta.get("provider_status") or "unknown"),
        "fallbackUsed": bool(provider_meta.get("fallback_used")),
        "humanAction": human_action,
        "setupType": setup_type,
        "targetSetupType": target_setup_type,
        "observedSetupType": observed_setup_type,
        "zoneStatus": (engine_a_ctx or {}).get("zone_status"),
        "watchState": watch_state if isinstance(watch_state, dict) else None,
        "executionPermitted": (engine_a_ctx or {}).get("execution_permitted"),
        "symbolAliasApplied": (engine_a_ctx or {}).get("symbol_alias_applied"),
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
        "finalReason": resolve_watch_reason(
            plan=ai_review.get("suggestedTradePlan") or ai_review.get("suggested_trade_plan"),
            summary={"finalReason": ms.get("finalReason")},
            review=ai_review,
        )
        or _final_reason(ai_review, concordance, human_action),
        "engineA": snapshots.get("engineA"),
        "engineB": snapshots.get("engineB"),
        "engineC": snapshots.get("engineC"),
        "engineD": engine_d,
    }
