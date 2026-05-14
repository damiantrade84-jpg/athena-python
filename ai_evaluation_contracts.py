"""AI Evaluation data contracts for measuring AI review quality vs trade outcomes.

All fields are advisory. No field grants execution permission or config mutation
authority. All reports default do_not_auto_apply=True.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class AIEvaluationSample(BaseModel):
    """One AI review event, linked to an actual trade outcome when available."""

    model_config = ConfigDict(extra="allow")

    sample_id: str = ""
    trace_id: str | None = None
    symbol: str = ""
    asset_type: str = ""
    engine: str = ""
    timeframe: str = ""
    setup_type: str = ""
    ai_surface: str = "MARCUS"
    ai_decision: str | None = None
    ai_grade: str | None = None
    ai_confidence: float | None = None
    ai_blocked: bool = False
    ai_downgraded: bool = False
    ai_requested_confirmation: bool = False
    review_type: str | None = None
    deterministic_decision_before_ai: bool = False
    deterministic_decision_after_ai: bool = False
    actual_outcome: str | None = None
    realized_r: float | None = None
    exit_reason: str | None = None
    holding_bars: int | None = None
    outcome_timestamp: str | None = None
    sample_valid: bool = False
    missing_outcome_reason: str | None = None
    is_simulated: bool = False
    ai_changed_execution_permission: bool | None = None
    contradiction_flags: list[str] = Field(default_factory=list)
    parse_success: bool = True
    data_freshness: str | None = None
    vision_freshness: str | None = None


class AISurfaceMetrics(BaseModel):
    """Aggregated metrics for one AI surface."""

    model_config = ConfigDict(extra="allow")

    ai_surface: str = ""
    sample_count: int = 0
    valid_outcome_count: int = 0
    insufficient_count: int = 0
    win_rate_when_positive: float | None = None
    avg_r_when_positive: float | None = None
    block_count: int = 0
    useful_block_count: int = 0
    false_block_count: int = 0
    harmful_allow_count: int = 0
    calibration_error: float | None = None
    contradiction_rate: float | None = None
    missing_data_rate: float | None = None
    parse_failure_rate: float | None = None
    stale_context_rate: float | None = None
    notes: list[str] = Field(default_factory=list)


class AIEvaluationReport(BaseModel):
    """Complete AI evaluation report across all surfaces."""

    model_config = ConfigDict(extra="allow")

    report_id: str = ""
    generated_at: str = Field(default_factory=_now_iso)
    lookback_days: int = 30
    surfaces: list[str] = Field(default_factory=list)
    overall_summary: str = ""
    surface_metrics: dict[str, AISurfaceMetrics] = Field(default_factory=dict)
    best_ai_behaviors: list[str] = Field(default_factory=list)
    worst_ai_behaviors: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    do_not_auto_apply: bool = True
    warnings: list[str] = Field(default_factory=list)

    @field_validator("do_not_auto_apply")
    @classmethod
    def _no_auto_apply(cls, v: bool) -> bool:
        return True


class AIRecommendationEvaluation(BaseModel):
    """Track whether an AI recommendation was accepted and its outcome."""

    model_config = ConfigDict(extra="allow")

    recommendation_id: str = ""
    source: str = ""
    accepted_by_user: bool = False
    tested: bool = False
    result: str | None = None
    improved: bool | None = None
    worsened: bool | None = None
    notes: str = ""
