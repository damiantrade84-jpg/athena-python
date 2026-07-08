"""ai_schemas.py — Pydantic models for xAI structured output responses.

These schemas enforce valid JSON responses from the AI, eliminating manual parsing.
Used with client.beta.chat.completions.parse(response_format=Model).
"""

from enum import Enum
from typing import Any, List, Optional
from pydantic import BaseModel, Field, field_validator


class RiskLevel(str, Enum):
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"


class TradeStyle(str, Enum):
    SCALP = "SCALP"
    INTRADAY = "INTRADAY"
    SWING = "SWING"


class AIGrade(str, Enum):
    A_PLUS = "A+"
    A = "A"
    B = "B"
    C = "C"
    D = "D"
    F = "F"


class StyleRating(BaseModel):
    """Per-style rating (scalp / intraday / swing)."""

    grade: str = Field(description="Trade grade: A+, A, B, C, D, or F")
    edgeProbability: float = Field(
        ge=0, le=100, description="Win probability estimate 0-100"
    )
    riskLevel: str = Field(description="Low, Medium, or High")


class EngineAResponse(BaseModel):
    """Schema for main AI analysis response (Marcus Reid Engine A)."""

    symbol: str = Field(description="Signal symbol or pair, e.g. BTCUSDT")
    timeframe: str = Field(description="Primary evaluated timeframe, e.g. 15m/H1/H4/D1")
    bias: str = Field(description="long, short, or neutral")
    setup_type: str = Field(description="Short setup label, e.g. breakout_retest")
    trend_score: float = Field(ge=0, le=100, description="Trend component score")
    structure_score: float = Field(ge=0, le=100, description="Structure component score")
    momentum_score: float = Field(ge=0, le=100, description="Momentum component score")
    liquidity_score: float = Field(ge=0, le=100, description="Liquidity component score")
    risk_score: float = Field(ge=0, le=100, description="Risk quality component score")
    confirmation_score: float = Field(ge=0, le=100, description="Confirmation component score")
    total_score: float = Field(ge=0, le=100, description="Total AI quality score")
    grade: str = Field(description="Trade grade: A+, A, B, C, D, or F")
    ai_action: str = Field(
        description="ignore, watchlist_only, needs_confirmation, high_quality_review, or reject"
    )
    blocking_reasons: List[str] = Field(
        default_factory=list,
        description="Machine-readable AI-observed blockers. ATHENA hard rules remain authoritative.",
    )
    reason: str = Field(description="One concise reason for the score/action")
    verdict: str = Field(description="One punchy sentence assessment")
    narrative: str = Field(description="2-3 sentences referencing specific data")
    entryZone: str = Field(description="Exact entry price or fib level")
    invalidation: str = Field(description="Price level that invalidates the trade")
    keyLevels: str = Field(description="Key S/R levels (S1/R1 format)")
    levelsVerdict: Optional[str] = Field(
        default=None,
        description="Advisory SL/TP review: accept, adjust, or reject",
    )
    levelsReason: Optional[str] = Field(
        default=None,
        description="Evidence cited for levelsVerdict",
    )
    suggestedSL: Optional[str] = Field(
        default=None,
        description="Advisory stop suggestion when levelsVerdict is adjust/reject",
    )
    suggestedTP: Optional[str] = Field(
        default=None,
        description="Advisory target suggestion when levelsVerdict is adjust/reject",
    )
    positionSizing: str = Field(description="Full/Half/Quarter with R explanation")
    tradeStyle: str = Field(description="SWING, INTRADAY, or SCALP")
    tradeStyleReason: str = Field(description="Why this style fits")
    warnings: List[str] = Field(description="Specific risk warnings")
    edgeProbability: float = Field(
        ge=0, le=100, description="Win probability estimate 0-100"
    )
    riskLevel: str = Field(description="Low, Medium, or High")
    style_ratings: Optional[dict] = Field(
        default=None,
        description="Per-style ratings: {scalp: {grade, edgeProbability, riskLevel}, intraday: {...}, swing: {...}}",
    )


def _coerce_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _first_number(*values: Any) -> float | None:
    for value in values:
        number = _coerce_float(value)
        if number is not None:
            return number
    return None


def _nested_first_number(source: dict, keys: tuple[str, ...]) -> float | None:
    for key in keys:
        number = _coerce_float(source.get(key))
        if number is not None:
            return number
    return None


def _signal_stop_loss(signal: dict) -> float | None:
    direct = _nested_first_number(
        signal,
        (
            "sl",
            "stop",
            "stopLoss",
            "stop_loss",
            "final_stop_loss",
            "recommended_stop_loss",
        ),
    )
    if direct is not None:
        return direct
    for nested_key in ("levels", "risk", "riskState", "naked_data", "engine_b"):
        nested = signal.get(nested_key)
        if isinstance(nested, dict):
            found = _nested_first_number(
                nested,
                (
                    "sl",
                    "stop",
                    "stopLoss",
                    "stop_loss",
                    "final_stop_loss",
                    "recommended_stop_loss",
                    "execution_sl",
                ),
            )
            if found is not None:
                return found
    return None


def _signal_rr(signal: dict) -> float | None:
    direct = _nested_first_number(
        signal,
        (
            "rr1",
            "rr",
            "riskReward",
            "risk_reward",
            "rr_used_for_gate",
            "execution_rr",
        ),
    )
    if direct is not None:
        return direct
    for nested_key in ("levels", "risk", "riskState", "naked_data", "engine_b"):
        nested = signal.get(nested_key)
        if isinstance(nested, dict):
            found = _nested_first_number(
                nested,
                (
                    "rr1",
                    "rr",
                    "riskReward",
                    "risk_reward",
                    "rr_used_for_gate",
                    "execution_rr",
                ),
            )
            if found is not None:
                return found
    return None


def _daily_loss_limit_hit(signal: dict) -> bool:
    direct_keys = (
        "dailyLossLimitHit",
        "daily_loss_limit_hit",
        "daily_loss_limit_reached",
        "max_daily_losses_hit",
    )
    if any(bool(signal.get(key)) for key in direct_keys):
        return True
    for nested_key in ("risk", "riskState", "portfolio", "portfolioState"):
        nested = signal.get(nested_key)
        if isinstance(nested, dict) and any(bool(nested.get(key)) for key in direct_keys):
            return True
    return False


def _high_impact_news_nearby(signal: dict, news_ctx: dict | None) -> bool:
    event_keys = ("highImpactNewsNearby", "high_impact_news_nearby", "news_blocked")
    if any(bool(signal.get(key)) for key in event_keys):
        return True
    event_risk = signal.get("eventRisk") or signal.get("event_risk") or {}
    if isinstance(event_risk, dict) and (
        event_risk.get("blocked")
        or event_risk.get("highImpactNearby")
        or event_risk.get("high_impact_nearby")
    ):
        return True
    ctx = news_ctx if isinstance(news_ctx, dict) else {}
    return bool(ctx.get("forexEvents") or ctx.get("highImpactEvents"))


def evaluate_engine_a_ai_advisory_rules(
    ai_result: dict,
    signal: dict,
    news_ctx: dict | None = None,
    *,
    min_rr: float = 1.5,
) -> dict:
    """
    Apply deterministic advisory hard rules to Marcus output.

    This does not replace live risk gates and must not be treated as permission
    to execute. It creates machine-checkable review fields for UI/audit use.
    """
    score = _first_number(
        ai_result.get("total_score"),
        ai_result.get("edgeProbability"),
    )
    if score is None:
        max_score = _coerce_float(signal.get("maxScore")) or 0.0
        confluence = _coerce_float(signal.get("confluenceScore"))
        if confluence is not None and max_score > 0:
            score = max(0.0, min(100.0, confluence / max_score * 100.0))

    blocking_reasons: list[str] = []
    action = "review_incomplete"
    bucket = "score_unavailable"
    if score is None:
        blocking_reasons.append("AI_SCORE_UNAVAILABLE")
    elif score < 65:
        action = "ignore"
        bucket = "below_65_ignore"
    elif score < 75:
        action = "watchlist_only"
        bucket = "65_74_watchlist_only"
    elif score < 85:
        action = "needs_confirmation"
        bucket = "75_84_valid_needs_confirmation"
    else:
        action = "high_quality_review"
        bucket = "85_plus_high_quality"

    if _signal_stop_loss(signal) is None:
        blocking_reasons.append("NO_STOP_LOSS")

    rr = _signal_rr(signal)
    if rr is None:
        blocking_reasons.append("RR_UNAVAILABLE")
    elif rr < min_rr:
        blocking_reasons.append("RR_BELOW_MIN")

    if _daily_loss_limit_hit(signal):
        blocking_reasons.append("DAILY_LOSS_LIMIT_HIT")

    if _high_impact_news_nearby(signal, news_ctx):
        blocking_reasons.append("HIGH_IMPACT_NEWS_NEARBY")

    if blocking_reasons and action != "review_incomplete":
        action = "reject"

    return {
        "advisory_rule_trade_allowed": bool(
            action == "high_quality_review" and not blocking_reasons
        ),
        "advisory_rule_action": action,
        "advisory_rule_score": round(score, 2) if score is not None else None,
        "advisory_rule_bucket": bucket,
        "advisory_blocking_reasons": blocking_reasons,
        "advisory_min_rr": min_rr,
    }


_GRADE_EDGE_BANDS: dict[str, tuple[int, int]] = {
    "A+": (75, 95),
    "A": (70, 90),
    "B": (45, 65),
    "C": (30, 50),
    "D": (15, 35),
    "F": (5, 25),
}


def _grade_edge_midpoint(grade: str) -> int | None:
    band = _GRADE_EDGE_BANDS.get(str(grade or "").upper().strip())
    if not band:
        return None
    return (band[0] + band[1]) // 2


def _clamp_edge_to_grade_band(grade: str, edge: float) -> tuple[float, bool]:
    band = _GRADE_EDGE_BANDS.get(str(grade or "").upper().strip())
    if band is None:
        return edge, False
    low, high = band
    if low <= edge <= high:
        return edge, False
    midpoint = (low + high) // 2
    return float(midpoint), True


def enforce_marcus_grade_edge_consistency(result: dict) -> dict:
    """Clamp edgeProbability toward grade-consistent bands; append warning on mismatch."""
    if not isinstance(result, dict):
        return result

    warnings = list(result.get("warnings") or [])
    adjusted = False

    grade = str(result.get("grade") or "").upper().strip()
    edge = _coerce_float(result.get("edgeProbability"))
    if grade and edge is not None:
        clamped, changed = _clamp_edge_to_grade_band(grade, edge)
        if changed:
            result["edgeProbability"] = round(clamped)
            adjusted = True

    style_ratings = result.get("style_ratings")
    if isinstance(style_ratings, dict):
        for style_key, style_val in style_ratings.items():
            if not isinstance(style_val, dict):
                continue
            s_grade = str(style_val.get("grade") or "").upper().strip()
            s_edge = _coerce_float(style_val.get("edgeProbability"))
            if not s_grade or s_edge is None:
                continue
            clamped, changed = _clamp_edge_to_grade_band(s_grade, s_edge)
            if changed:
                style_val["edgeProbability"] = round(clamped)
                adjusted = True

    if adjusted and "GRADE_EDGE_MISMATCH_ADJUSTED" not in warnings:
        warnings.append("GRADE_EDGE_MISMATCH_ADJUSTED")
        result["warnings"] = warnings

    return result


def _engine_b_gate_score(signal: dict) -> float | None:
    naked = signal.get("naked_data") if isinstance(signal.get("naked_data"), dict) else {}
    engine_b = signal.get("engine_b") if isinstance(signal.get("engine_b"), dict) else {}
    data = naked or engine_b
    if not data:
        return None

    gate_pct = _coerce_float(data.get("gate_pct"))
    if gate_pct is not None:
        return max(0.0, min(100.0, gate_pct))

    score = _coerce_float(data.get("score"))
    max_score = _coerce_float(data.get("max_possible")) or _coerce_float(data.get("maxScore"))
    score_pct = _coerce_float(data.get("score_pct"))
    if score is not None and max_score and max_score > 0:
        return max(0.0, min(100.0, score / max_score * 100.0))
    if score_pct is not None:
        return max(0.0, min(100.0, score_pct))
    return None


def _engine_b_structural_bucket(signal: dict) -> tuple[str, str, float | None]:
    naked = signal.get("naked_data") if isinstance(signal.get("naked_data"), dict) else {}
    engine_b = signal.get("engine_b") if isinstance(signal.get("engine_b"), dict) else {}
    data = naked or engine_b

    verdict = str(data.get("structural_verdict") or "").upper()
    gate_flags = [
        data.get("structure_ok"),
        data.get("location_ok"),
        data.get("trigger_ok") or data.get("entry_ok"),
        data.get("rr_ok") or data.get("room_rr_ok"),
        data.get("room_ok"),
    ]
    failed_gates = sum(1 for flag in gate_flags if flag is False)
    score = _engine_b_gate_score(signal)

    if verdict == "CLEAR" and failed_gates == 0 and (score is None or score >= 75):
        return "high_quality_review", "engine_b_clear_gates_pass", score
    if verdict in {"CLEAR", "CAUTION"} and failed_gates <= 1 and (score is None or score >= 65):
        return "needs_confirmation", "engine_b_mixed_gates", score
    if score is not None and score >= 50:
        return "watchlist_only", "engine_b_partial_gates", score
    return "ignore", "engine_b_weak_structure", score


def _engine_c_consensus_bucket(signal: dict) -> tuple[str, str, float | None]:
    engine_c = signal.get("engine_c") if isinstance(signal.get("engine_c"), dict) else {}
    decision = str(
        engine_c.get("decision_state") or signal.get("decision_state") or ""
    ).lower()
    tier = str(engine_c.get("tier") or signal.get("tier") or "").upper()
    conviction = _coerce_float(
        engine_c.get("conviction")
        or signal.get("combinedConviction")
        or signal.get("conviction")
    )
    score = None
    if conviction is not None:
        score = max(0.0, min(100.0, conviction * 100.0 if conviction <= 1.0 else conviction))

    if decision in {"blocked", "reject", "no_trade"}:
        return "reject", "engine_c_blocked", score
    if decision in {"watchlist", "reduced_risk"}:
        return "watchlist_only", "engine_c_watchlist", score
    if tier == "HIGH" and (score is None or score >= 75):
        return "high_quality_review", "engine_c_high_tier", score
    if score is not None and score >= 65:
        return "needs_confirmation", "engine_c_moderate_conviction", score
    return "watchlist_only", "engine_c_low_conviction", score


def evaluate_marcus_advisory_rules(
    engine_source: str,
    ai_result: dict,
    signal: dict,
    news_ctx: dict | None = None,
    *,
    resolved_style: str = "intraday",
) -> dict:
    """Engine-aware advisory rules for Marcus text review."""
    from ai_context import resolve_ai_review_min_rr

    min_rr = resolve_ai_review_min_rr(signal, resolved_style)
    source = str(engine_source or "engine_a").lower()

    if source == "engine_b":
        action, bucket, score = _engine_b_structural_bucket(signal)
        edge = _coerce_float(ai_result.get("edgeProbability"))
        if score is None and edge is not None:
            score = edge
    elif source == "engine_c":
        action, bucket, score = _engine_c_consensus_bucket(signal)
        edge = _coerce_float(ai_result.get("edgeProbability"))
        if score is None and edge is not None:
            score = edge
    else:
        return evaluate_engine_a_ai_advisory_rules(
            ai_result,
            signal,
            news_ctx,
            min_rr=min_rr,
        )

    blocking_reasons: list[str] = []
    if _signal_stop_loss(signal) is None:
        blocking_reasons.append("NO_STOP_LOSS")

    rr = _signal_rr(signal)
    if rr is None:
        blocking_reasons.append("RR_UNAVAILABLE")
    elif rr < min_rr:
        blocking_reasons.append("RR_BELOW_MIN")

    if _daily_loss_limit_hit(signal):
        blocking_reasons.append("DAILY_LOSS_LIMIT_HIT")

    if _high_impact_news_nearby(signal, news_ctx):
        blocking_reasons.append("HIGH_IMPACT_NEWS_NEARBY")

    if blocking_reasons and action not in {"review_incomplete", "reject"}:
        action = "reject"

    return {
        "advisory_rule_trade_allowed": bool(
            action == "high_quality_review" and not blocking_reasons
        ),
        "advisory_rule_action": action,
        "advisory_rule_score": round(score, 2) if score is not None else None,
        "advisory_rule_bucket": bucket,
        "advisory_blocking_reasons": blocking_reasons,
        "advisory_min_rr": min_rr,
    }


class EngineBResponse(BaseModel):
    """Schema for Engine B (Naked Structure) AI response."""

    grade: str = Field(description="Trade grade: A+, A, B, C, D, or F")
    edgeProbability: float = Field(
        ge=0, le=100, description="Win probability estimate 0-100"
    )
    riskLevel: str = Field(description="LOW, MEDIUM, or HIGH")
    verdict: str = Field(description="Concise structural analysis")
    reviewSource: Optional[str] = Field(
        default=None,
        description="Review provenance label, e.g. engine_b_marcus",
    )
    levelsVerdict: Optional[str] = Field(
        default=None,
        description="Advisory SL/TP review: accept, adjust, or reject",
    )
    levelsReason: Optional[str] = Field(
        default=None,
        description="Evidence cited for levelsVerdict",
    )
    suggestedSL: Optional[str] = Field(
        default=None,
        description="Advisory stop suggestion when levelsVerdict is adjust/reject",
    )
    suggestedTP: Optional[str] = Field(
        default=None,
        description="Advisory target suggestion when levelsVerdict is adjust/reject",
    )
    style_ratings: Optional[dict] = Field(
        default=None,
        description="Per-style ratings: {scalp: {grade, edgeProbability, riskLevel}, intraday: {...}, swing: {...}}",
    )


class EngineCMarcusResponse(BaseModel):
    """Relaxed Marcus schema for Engine C consensus reviews."""

    grade: str = Field(description="Trade grade: A+, A, B, C, D, or F")
    edgeProbability: float = Field(ge=0, le=100, description="Win probability estimate 0-100")
    riskLevel: str = Field(description="Low, Medium, or High")
    verdict: str = Field(description="One punchy sentence assessment")
    reviewSource: Optional[str] = Field(default=None, description="engine_c_marcus")
    reason: Optional[str] = Field(default=None, description="Concise reason for grade/action")
    narrative: Optional[str] = Field(default=None, description="2-3 sentences referencing consensus data")
    warnings: Optional[List[str]] = Field(default=None, description="Specific risk warnings")
    style_ratings: Optional[dict] = Field(default=None, description="Per-style ratings")
    trend_score: Optional[float] = Field(default=None, ge=0, le=100)
    structure_score: Optional[float] = Field(default=None, ge=0, le=100)
    momentum_score: Optional[float] = Field(default=None, ge=0, le=100)
    liquidity_score: Optional[float] = Field(default=None, ge=0, le=100)
    risk_score: Optional[float] = Field(default=None, ge=0, le=100)
    confirmation_score: Optional[float] = Field(default=None, ge=0, le=100)
    total_score: Optional[float] = Field(default=None, ge=0, le=100)
    ai_action: Optional[str] = Field(default=None)


class DebateCaseResponse(BaseModel):
    """Schema for Bull/Bear debate case response."""

    conviction: int = Field(ge=0, le=10, description="Conviction level 0-10")
    key_arguments: List[str] = Field(description="Top 3 arguments for this case")
    risk_factors: Optional[List[str]] = Field(
        default=None, description="Risk factors (Bull case)"
    )
    counter_risks: Optional[List[str]] = Field(
        default=None, description="Counter risks (Bear case)"
    )


class DebateGrade(str, Enum):
    STRONG_GO = "STRONG_GO"
    WEAK_GO = "WEAK_GO"
    PASS = "PASS"
    STRONG_AVOID = "STRONG_AVOID"


class JudgeVerdictResponse(BaseModel):
    """Schema for Judge verdict in signal debate."""

    grade: str = Field(description="STRONG_GO, WEAK_GO, PASS, or STRONG_AVOID")
    reasoning: str = Field(description="1-2 sentence verdict explanation")
    score_adjustment: float = Field(
        ge=-1.0,
        le=1.0,
        description="Score delta proposed by judge; execution clamps to ≤ 0 (Audit CRIT-002).",
    )

    @field_validator("score_adjustment", mode="after")
    @classmethod
    def clamp_positive_adjustment(cls, v: float) -> float:
        """Debate may only reduce Engine A score, never increase it."""
        import logging

        try:
            x = float(v)
        except (TypeError, ValueError):
            return 0.0
        if x > 0:
            logging.getLogger("sentinel.debate").warning(
                "[DEBATE] score_adjustment=%.4f > 0 clamped to 0.0 (downgrade-only, CRIT-002)",
                x,
            )
            return 0.0
        return x


class MetaAnalysisResponse(BaseModel):
    """Schema for weekly meta-analysis response."""

    summary: str = Field(description="One sentence overall assessment")
    insights: List[str] = Field(description="Key insights from the data")
    adjustments: List[str] = Field(description="Specific adjustments to make")
    blind_spot: str = Field(description="One key systematic miss")
    devils_advocate: str = Field(
        description="Strongest counter-argument to conclusions"
    )


class EngineCWeightVerdictSchema(BaseModel):
    """Schema for Engine C AI weight verdict (Phase 0 schema hardening).

    Advisory-only: the verdict tweaks weights + conviction_modifier; it
    never flips trade from False to True. See engine_c_ai.py.
    """

    trust_verdict: str = Field(description="trust_a | trust_b | trust_both | trust_neither")
    weight_recommendation: Optional[dict] = Field(
        default=None,
        description='{"A": float, "B": float} summing to ~1.0, each in [0.2, 0.8]',
    )
    conviction_modifier: float = Field(
        default=0.0,
        ge=-0.15,
        le=0.15,
        description="Edge-only conviction tweak in [-0.15, 0.15]",
    )
    reasoning: str = Field(default="", description="Cited reasoning from the packet")
