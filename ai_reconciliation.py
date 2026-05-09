"""
ai_reconciliation.py — AI Verdict Reconciliation Layer

Compares the Marcus Reid text-summary verdict (Engine A/B AI analysis)
with the Chart Vision visual verdict and produces a single reconciled
ai_agreement score and label.

Design principles:
  - Display-only. No effect on Engine C conviction or any execution gate.
  - Both AIs are advisory. This layer aggregates their advisory signals.
  - Score is 0.0–1.0 (higher = stronger agreement on the same direction).
  - Conflict (one says AVOID while the other says STRONG) is surfaced
    explicitly so the user can make an informed judgment.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("sentinel")

AI_DECISION_ALLOW = "ALLOW"
AI_DECISION_BLOCK = "BLOCK"
AI_STATE_CONFIRM = "CONFIRM"
AI_STATE_CAUTION = "CAUTION"
AI_STATE_REJECT = "REJECT"
AI_STATE_REVIEW_INCOMPLETE = "REVIEW_INCOMPLETE"

_DEBATE_CONFIRM = {"STRONG_GO", "WEAK_GO"}
_DEBATE_REJECT = {"PASS", "STRONG_AVOID"}
_DEBATE_NEUTRAL = {"SKIP"}
_DEBATE_FAILURE = {"ERROR"}
_VISION_CONFIRM = {"CONFIRM", "CONFIRMS", "STRONG", "MODERATE"}
_VISION_CAUTION = {"CAUTION", "REVIEW", "WEAK"}
_VISION_REJECT = {
    "REJECT",
    "AVOID",
    "CONTRADICT",
    "CONTRADICTS",
    "POTENTIAL REVERSAL",
    "POTENTIAL_REVERSAL",
}


def _upper_token(value: Any) -> str:
    return str(value or "").strip().upper().replace("-", "_")


def _extract_trace_id(verdicts: dict[str, Any]) -> str | None:
    trace_id = verdicts.get("trace_id")
    if trace_id:
        return str(trace_id)
    for source in ("debate", "vision", "engine_b", "marcus"):
        item = verdicts.get(source)
        if isinstance(item, dict) and item.get("trace_id"):
            return str(item.get("trace_id"))
    return None


def _source_result(
    *,
    source: str,
    state: str,
    category: str,
    reason: str,
    raw: str | None = None,
) -> dict[str, Any]:
    return {
        "source": source,
        "state": state,
        "category": category,
        "reason": reason,
        "raw": raw,
    }


def _arbitrate_debate(debate: Any) -> dict[str, Any]:
    if not isinstance(debate, dict):
        return _source_result(
            source="debate",
            state=AI_STATE_REJECT,
            category="invalid",
            reason="DEBATE_SCHEMA_INVALID",
        )
    if debate.get("schema_valid") is False or debate.get("parse_success") is False:
        return _source_result(
            source="debate",
            state=AI_STATE_REJECT,
            category="invalid",
            reason="DEBATE_SCHEMA_INVALID",
        )
    grade = _upper_token(debate.get("grade"))
    allowed = debate.get("allowed")
    if grade in _DEBATE_FAILURE or debate.get("error"):
        return _source_result(
            source="debate",
            state=AI_STATE_REJECT,
            category="failure",
            reason="DEBATE_AI_FAILURE",
            raw=grade or None,
        )
    if not grade or not isinstance(allowed, bool):
        return _source_result(
            source="debate",
            state=AI_STATE_REJECT,
            category="invalid",
            reason="DEBATE_SCHEMA_INVALID",
            raw=grade or None,
        )
    if grade in _DEBATE_REJECT or allowed is False:
        return _source_result(
            source="debate",
            state=AI_STATE_REJECT,
            category="reject",
            reason=f"DEBATE_{grade or 'BLOCKED'}",
            raw=grade,
        )
    if grade in _DEBATE_CONFIRM:
        return _source_result(
            source="debate",
            state=AI_STATE_CONFIRM,
            category="support",
            reason=f"DEBATE_{grade}",
            raw=grade,
        )
    if grade in _DEBATE_NEUTRAL:
        return _source_result(
            source="debate",
            state=AI_STATE_REVIEW_INCOMPLETE,
            category="neutral",
            reason="DEBATE_SKIPPED",
            raw=grade,
        )
    return _source_result(
        source="debate",
        state=AI_STATE_REJECT,
        category="invalid",
        reason="DEBATE_SCHEMA_INVALID",
        raw=grade,
    )


def _vision_token(vision: dict[str, Any]) -> str:
    structured = vision.get("structured") if isinstance(vision.get("structured"), dict) else {}
    raw = (
        vision.get("ai_review_state")
        or vision.get("state")
        or vision.get("right_edge_status")
        or structured.get("right_edge_status")
        or structured.get("rating")
        or vision.get("vision_rating")
        or vision.get("rating")
    )
    token = _upper_token(raw).replace("_", " ")
    if token:
        return token
    text = str(vision.get("analysis") or "").upper()
    for candidate in (
        "POTENTIAL REVERSAL",
        "CONTRADICTS",
        "CONTRADICT",
        "AVOID",
        "CONFIRMS",
        "CONFIRM",
        "STRONG",
        "MODERATE",
        "REVIEW",
        "WEAK",
    ):
        if candidate in text:
            return candidate
    return ""


def _arbitrate_vision(vision: Any) -> dict[str, Any]:
    if not isinstance(vision, dict):
        return _source_result(
            source="vision",
            state=AI_STATE_REJECT,
            category="invalid",
            reason="VISION_SCHEMA_INVALID",
        )
    if vision.get("schema_valid") is False or vision.get("parse_success") is False:
        return _source_result(
            source="vision",
            state=AI_STATE_REJECT,
            category="invalid",
            reason="VISION_SCHEMA_INVALID",
        )
    token = _vision_token(vision)
    structured = vision.get("structured") if isinstance(vision.get("structured"), dict) else {}
    confirms_direction = structured.get("confirms_direction", vision.get("confirms_direction"))
    if vision.get("error"):
        return _source_result(
            source="vision",
            state=AI_STATE_REJECT,
            category="failure",
            reason="VISION_AI_FAILURE",
            raw=token or None,
        )
    if confirms_direction is False:
        return _source_result(
            source="vision",
            state=AI_STATE_REJECT,
            category="reject",
            reason="VISION_CONTRADICTS_DIRECTION",
            raw=token or None,
        )
    if token in _VISION_REJECT:
        return _source_result(
            source="vision",
            state=AI_STATE_REJECT,
            category="reject",
            reason=f"VISION_{token.replace(' ', '_')}",
            raw=token,
        )
    if token in _VISION_CONFIRM:
        return _source_result(
            source="vision",
            state=AI_STATE_CONFIRM,
            category="support",
            reason=f"VISION_{token.replace(' ', '_')}",
            raw=token,
        )
    if token in _VISION_CAUTION:
        return _source_result(
            source="vision",
            state=AI_STATE_CAUTION,
            category="neutral",
            reason=f"VISION_{token.replace(' ', '_')}",
            raw=token,
        )
    return _source_result(
        source="vision",
        state=AI_STATE_REJECT,
        category="invalid",
        reason="VISION_SCHEMA_INVALID",
        raw=token or None,
    )


def arbitrate_ai(
    verdicts: dict[str, Any] | None,
    *,
    require_trace_id: bool = True,
    required_sources: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Return one deterministic AI execution decision from advisory AI verdicts.

    This function is pure: it reads only its inputs and performs no logging,
    network calls, persistence, scoring, or model prompting.
    """
    result: dict[str, Any] = {
        "decision": AI_DECISION_ALLOW,
        "execution_allowed": True,
        "ai_review_state": AI_STATE_REVIEW_INCOMPLETE,
        "reason": "NO_AI_VERDICTS",
        "trace_id": None,
        "schema_valid": True,
        "blocking_sources": [],
        "supporting_sources": [],
        "neutral_sources": [],
        "missing_sources": [],
        "conflict_flags": [],
        "source_states": {},
    }
    required_sources = tuple(s for s in required_sources if s in ("debate", "vision"))
    if verdicts is None:
        if required_sources:
            result.update(
                {
                    "decision": AI_DECISION_BLOCK,
                    "execution_allowed": False,
                    "ai_review_state": AI_STATE_REJECT,
                    "reason": "AI_REQUIRED_SOURCES_MISSING",
                    "blocking_sources": list(required_sources),
                    "missing_sources": list(required_sources),
                }
            )
        return result
    if not isinstance(verdicts, dict):
        result.update(
            {
                "decision": AI_DECISION_BLOCK,
                "execution_allowed": False,
                "ai_review_state": AI_STATE_REJECT,
                "reason": "AI_VERDICTS_SCHEMA_INVALID",
                "schema_valid": False,
                "blocking_sources": ["schema"],
            }
        )
        return result

    trace_id = _extract_trace_id(verdicts)
    result["trace_id"] = trace_id
    source_states: dict[str, dict[str, Any]] = {}
    for source, parser in (
        ("debate", _arbitrate_debate),
        ("vision", _arbitrate_vision),
    ):
        if source in verdicts and verdicts.get(source) is not None:
            source_states[source] = parser(verdicts.get(source))
        else:
            result["missing_sources"].append(source)

    result["source_states"] = source_states
    has_verdict = bool(source_states)
    missing_required = [s for s in required_sources if s not in source_states]
    if missing_required:
        result.update(
            {
                "decision": AI_DECISION_BLOCK,
                "execution_allowed": False,
                "ai_review_state": AI_STATE_REJECT,
                "reason": "AI_REQUIRED_SOURCES_MISSING",
                "blocking_sources": missing_required,
            }
        )
        return result
    if require_trace_id and has_verdict and not trace_id:
        result.update(
            {
                "decision": AI_DECISION_BLOCK,
                "execution_allowed": False,
                "ai_review_state": AI_STATE_REJECT,
                "reason": "TRACE_ID_MISSING",
                "blocking_sources": ["trace_id"],
            }
        )
        return result

    blockers = [
        state["source"]
        for state in source_states.values()
        if state["category"] in ("reject", "failure", "invalid")
    ]
    supporters = [
        state["source"]
        for state in source_states.values()
        if state["category"] == "support"
    ]
    neutrals = [
        state["source"]
        for state in source_states.values()
        if state["category"] == "neutral"
    ]
    result["blocking_sources"] = blockers
    result["supporting_sources"] = supporters
    result["neutral_sources"] = neutrals
    invalid_sources = [s for s in source_states.values() if s["category"] == "invalid"]
    if invalid_sources:
        result["schema_valid"] = False

    if blockers:
        if supporters:
            result["conflict_flags"].append("AI_CONFLICT_BLOCKER_WINS")
        first_blocker = next(
            state for state in source_states.values()
            if state["category"] in ("reject", "failure", "invalid")
        )
        result.update(
            {
                "decision": AI_DECISION_BLOCK,
                "execution_allowed": False,
                "ai_review_state": AI_STATE_REJECT,
                "reason": first_blocker["reason"],
            }
        )
        return result

    if supporters:
        result.update(
            {
                "decision": AI_DECISION_ALLOW,
                "execution_allowed": True,
                "ai_review_state": AI_STATE_CONFIRM,
                "reason": "AI_SUPPORTS_EXECUTION",
            }
        )
        return result

    if neutrals:
        result.update(
            {
                "decision": AI_DECISION_ALLOW,
                "execution_allowed": True,
                "ai_review_state": AI_STATE_REVIEW_INCOMPLETE,
                "reason": "AI_NEUTRAL_OR_SKIPPED",
            }
        )
    return result

# ── Grade → numeric map (Marcus Reid / Engine B AI) ──────────────────────────
# A+ = 5, A = 4.5, B = 3.5, C = 2.5, D = 1.5, F = 0.5
_GRADE_SCORE: dict[str, float] = {
    "A+": 5.0,
    "A":  4.5,
    "A-": 4.0,
    "B+": 3.8,
    "B":  3.5,
    "B-": 3.2,
    "C+": 2.8,
    "C":  2.5,
    "C-": 2.2,
    "D":  1.5,
    "D-": 1.0,
    "F":  0.5,
}

# ── Vision rating → numeric map ───────────────────────────────────────────────
# Maps the structured RATING values emitted by vision_prompts footers.
_VISION_SCORE: dict[str, float] = {
    "STRONG":     4.5,
    "MODERATE":   3.0,
    "WEAK":       1.5,
    "AVOID":      0.0,
    "CONTRADICTS": 0.0,
}

# Agreement level labels
_AGREEMENT_LABELS = [
    (0.85, "STRONG_AGREE"),
    (0.65, "MODERATE_AGREE"),
    (0.40, "WEAK_AGREE"),
    (0.20, "LOW_AGREE"),
    (0.00, "CONFLICT"),
]


def _grade_to_numeric(grade: str | None) -> float | None:
    """Convert a letter grade to numeric conviction (0–5 scale). Returns None if unreadable."""
    if not grade:
        return None
    g = str(grade).strip().upper()
    if g in _GRADE_SCORE:
        return _GRADE_SCORE[g]
    # Try partial match: "A+" from noisy text
    for key, val in _GRADE_SCORE.items():
        if g.startswith(key):
            return val
    return None


def _vision_rating_to_numeric(rating: str | None) -> float | None:
    """Convert a vision RATING token to numeric conviction (0–5 scale). Returns None if unreadable."""
    if not rating:
        return None
    r = str(rating).strip().upper()
    if r in _VISION_SCORE:
        return _VISION_SCORE[r]
    if "CONTRADICT" in r or "AVOID" in r:
        return 0.0
    if "STRONG" in r:
        return 4.5
    if "MODERATE" in r:
        return 3.0
    if "WEAK" in r:
        return 1.5
    return None


def _resolve_agreement_label(score: float) -> str:
    for threshold, label in _AGREEMENT_LABELS:
        if score >= threshold:
            return label
    return "CONFLICT"


def compute_ai_agreement(
    marcus_result: dict | None,
    vision_result: dict | None,
) -> dict:
    """
    Compare Marcus Reid text-summary and Chart Vision results to
    produce a reconciled AI agreement assessment.

    Args:
        marcus_result: Output from get_engine_b_ai_verdict() or equivalent
                       Engine A AI verdict. Expected keys: grade, edgeProbability,
                       riskLevel, verdict, style_ratings.
        vision_result: Output from chart vision call. Expected keys: analysis (text),
                       structured {rating, confirms_direction}, vision_rating (if
                       already applied by engine_c).

    Returns:
        dict with:
          ai_agreement_score: float 0.0–1.0
          ai_agreement_label: str  e.g. "STRONG_AGREE", "CONFLICT"
          marcus_numeric:     float | None (internal 0–5 scale)
          vision_numeric:     float | None (internal 0–5 scale)
          marcus_grade:       str | None
          vision_rating:      str | None
          vision_edge_prob:   int | None (if vision provided edgeProbability)
          marcus_edge_prob:   int | None
          conflict_flags:     list[str]  human-readable reasons for any conflict
          both_available:     bool
    """
    result: dict = {
        "ai_agreement_score": None,
        "ai_agreement_label": "UNAVAILABLE",
        "marcus_numeric": None,
        "vision_numeric": None,
        "marcus_grade": None,
        "vision_rating": None,
        "vision_edge_prob": None,
        "marcus_edge_prob": None,
        "conflict_flags": [],
        "both_available": False,
    }

    # ── Parse Marcus Reid ── ─────────────────────────────────────────────────
    m_grade = None
    m_numeric = None
    m_edge = None
    if isinstance(marcus_result, dict) and not marcus_result.get("error"):
        m_grade = marcus_result.get("grade")
        m_numeric = _grade_to_numeric(m_grade)
        try:
            m_edge = int(marcus_result.get("edgeProbability", 0))
        except (TypeError, ValueError):
            m_edge = None

    # ── Parse Vision ─────────────────────────────────────────────────────────
    v_rating = None
    v_numeric = None
    v_edge = None
    if isinstance(vision_result, dict) and not vision_result.get("error"):
        # Try structured dict first (engine_c already parsed it)
        structured = vision_result.get("structured") or {}
        v_rating = structured.get("rating") or vision_result.get("vision_rating")

        # Fallback: scan analysis text for rating tokens
        if not v_rating:
            text = (vision_result.get("analysis") or "").upper()
            for tok in ("STRONG", "MODERATE", "WEAK", "AVOID", "CONTRADICTS"):
                if tok in text:
                    v_rating = tok
                    break

        v_numeric = _vision_rating_to_numeric(v_rating)

        try:
            v_edge = int(vision_result.get("edgeProbability", 0))
        except (TypeError, ValueError):
            v_edge = None

    result["marcus_grade"] = m_grade
    result["vision_rating"] = v_rating
    result["marcus_edge_prob"] = m_edge
    result["vision_edge_prob"] = v_edge
    result["marcus_numeric"] = round(m_numeric, 3) if m_numeric is not None else None
    result["vision_numeric"] = round(v_numeric, 3) if v_numeric is not None else None

    # ── Compute agreement ─────────────────────────────────────────────────────
    if m_numeric is None and v_numeric is None:
        result["ai_agreement_label"] = "UNAVAILABLE"
        return result

    if m_numeric is None:
        # Only Vision available — report solo
        result["ai_agreement_score"] = round(v_numeric / 5.0, 4)
        result["ai_agreement_label"] = "VISION_ONLY"
        result["both_available"] = False
        return result

    if v_numeric is None:
        # Only Marcus available — report solo
        result["ai_agreement_score"] = round(m_numeric / 5.0, 4)
        result["ai_agreement_label"] = "MARCUS_ONLY"
        result["both_available"] = False
        return result

    result["both_available"] = True

    # Both available — compute normalized agreement on 0–1 scale
    m_norm = m_numeric / 5.0      # 0.0 – 1.0
    v_norm = v_numeric / 5.0      # 0.0 – 1.0

    # Agreement score: proximity between the two normalised signals.
    # Maximum disagreement (0 vs 1) → agreement = 0; perfect match → 1.
    proximity = 1.0 - abs(m_norm - v_norm)

    # Weighted average of both signals with proximity as a scaling factor.
    # avg_signal: mid-point conviction shared by both AIs
    avg_signal = (m_norm + v_norm) / 2.0
    # agreement_score: conviction × agreement (penalises mixed signals)
    agreement_score = round(avg_signal * proximity, 4)

    result["ai_agreement_score"] = agreement_score
    result["ai_agreement_label"] = _resolve_agreement_label(agreement_score)

    # ── Conflict flags ────────────────────────────────────────────────────────
    flags: list[str] = []

    # Hard conflict: one says veto (AVOID/CONTRADICTS or F/D), the other says strong
    marcus_positive = m_numeric >= 3.5   # B or above
    marcus_negative = m_numeric <= 1.5   # D or below
    vision_positive = v_numeric >= 3.0   # MODERATE or STRONG
    vision_veto     = v_numeric == 0.0   # AVOID or CONTRADICTS

    if vision_veto and marcus_positive:
        flags.append(
            f"HARD_CONFLICT: Vision={v_rating} vetoes the trade; "
            f"Marcus grades {m_grade} (positive). Vision wins in Engine C."
        )
    if marcus_negative and vision_positive:
        flags.append(
            f"ADVISORY_CONFLICT: Marcus grades {m_grade} (weak/reject); "
            f"Vision={v_rating} is positive. Marcus has no scoring effect."
        )

    # Edge probability divergence (if both provided)
    if m_edge is not None and v_edge is not None:
        delta = abs(m_edge - v_edge)
        if delta >= 25:
            flags.append(
                f"EDGE_PROB_DIVERGENCE: Marcus={m_edge}% vs Vision={v_edge}% "
                f"(gap={delta}%) — high uncertainty about true edge."
            )

    # Score proximity flag
    if proximity < 0.30 and not vision_veto:
        flags.append(
            f"RATING_MISMATCH: Marcus={m_grade} ({m_norm:.0%}) vs Vision={v_rating} "
            f"({v_norm:.0%}) — significant conviction gap between AIs."
        )

    result["conflict_flags"] = flags

    log.debug(
        "[AI_RECONCILE] marcus=%s(%.2f) vision=%s(%.2f) → agreement=%.3f label=%s flags=%d",
        m_grade, m_norm, v_rating, v_norm,
        agreement_score, result["ai_agreement_label"], len(flags),
    )

    return result


def reconcile_style_ratings(
    marcus_result: dict | None,
    vision_result: dict | None,
) -> dict:
    """
    Reconcile per-style (scalp/intraday/swing) AI ratings between Marcus and Vision.

    Marcus returns style_ratings[style][grade/edgeProbability/riskLevel].
    Vision returns per-style ratings via vision_style_ratings[style] = STRONG|MODERATE|...

    Returns dict keyed by style with reconciled agreement per style.
    """
    styles = ("scalp", "intraday", "swing")
    out: dict = {}

    marcus_styles: dict = {}
    if isinstance(marcus_result, dict) and not marcus_result.get("error"):
        marcus_styles = marcus_result.get("style_ratings") or {}

    vision_styles: dict = {}
    if isinstance(vision_result, dict) and not vision_result.get("error"):
        vision_styles = (
            vision_result.get("vision_style_ratings")
            or vision_result.get("style_ratings")
            or {}
        )

    for style in styles:
        m_style = marcus_styles.get(style, {}) if isinstance(marcus_styles.get(style), dict) else {}
        m_grade = m_style.get("grade") if m_style else None
        v_rating = vision_styles.get(style) if vision_styles else None

        style_agreement = compute_ai_agreement(
            {"grade": m_grade, "edgeProbability": m_style.get("edgeProbability")} if m_grade else None,
            {"vision_rating": v_rating} if v_rating else None,
        )
        out[style] = {
            "marcus_grade": m_grade,
            "vision_rating": v_rating,
            "agreement_score": style_agreement.get("ai_agreement_score"),
            "agreement_label": style_agreement.get("ai_agreement_label"),
            "conflict_flags": style_agreement.get("conflict_flags", []),
        }

    return out
