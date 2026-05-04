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


class EngineBResponse(BaseModel):
    """Schema for Engine B (Naked Structure) AI response."""

    grade: str = Field(description="Trade grade: A+, A, B, C, D, or F")
    edgeProbability: float = Field(
        ge=0, le=100, description="Win probability estimate 0-100"
    )
    riskLevel: str = Field(description="LOW, MEDIUM, or HIGH")
    verdict: str = Field(description="Concise structural analysis")
    style_ratings: Optional[dict] = Field(
        default=None,
        description="Per-style ratings: {scalp: {grade, edgeProbability, riskLevel}, intraday: {...}, swing: {...}}",
    )


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
