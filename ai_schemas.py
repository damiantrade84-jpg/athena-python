"""ai_schemas.py — Pydantic models for xAI structured output responses.

These schemas enforce valid JSON responses from the AI, eliminating manual parsing.
Used with client.beta.chat.completions.parse(response_format=Model).
"""

from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field


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


class EngineAResponse(BaseModel):
    """Schema for main AI analysis response (Marcus Reid Engine A)."""

    grade: str = Field(description="Trade grade: A+, A, B, C, D, or F")
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


class EngineBResponse(BaseModel):
    """Schema for Engine B (Naked Structure) AI response."""

    grade: str = Field(description="Trade grade: A+, A, B, C, D, or F")
    edgeProbability: float = Field(
        ge=0, le=100, description="Win probability estimate 0-100"
    )
    riskLevel: str = Field(description="LOW, MEDIUM, or HIGH")
    verdict: str = Field(description="Concise structural analysis")


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
        ge=-1.0, le=1.0, description="Score adjustment -1.0 to +1.0"
    )


class MetaAnalysisResponse(BaseModel):
    """Schema for weekly meta-analysis response."""

    summary: str = Field(description="One sentence overall assessment")
    insights: List[str] = Field(description="Key insights from the data")
    adjustments: List[str] = Field(description="Specific adjustments to make")
    blind_spot: str = Field(description="One key systematic miss")
    devils_advocate: str = Field(
        description="Strongest counter-argument to conclusions"
    )
