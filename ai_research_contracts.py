"""Canonical AI Research Agent contracts.

These models define the data contracts for the Research Agent layer.
They are advisory-only and must not grant execution permission or
config mutation authority. All recommendations default to do_not_auto_apply=True.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ResearchResultSummary(BaseModel):
    """Aggregated summary of completed research/backtest runs."""

    model_config = ConfigDict(extra="allow")

    run_id: str = ""
    source: str = "unknown"
    created_at: str = Field(default_factory=_now_iso)
    mode: str = "unknown"
    engines: list[str] = Field(default_factory=list)
    strategy_families: list[str] = Field(default_factory=list)
    symbols: list[str] = Field(default_factory=list)
    timeframes: list[str] = Field(default_factory=list)
    asset_groups: list[str] = Field(default_factory=list)
    total_trades: int = 0
    sample_quality: str = "unknown"
    top_clusters: list[dict[str, Any]] = Field(default_factory=list)
    weak_clusters: list[dict[str, Any]] = Field(default_factory=list)
    regime_findings: dict[str, Any] = Field(default_factory=dict)
    timeframe_findings: dict[str, Any] = Field(default_factory=dict)
    session_findings: dict[str, Any] = Field(default_factory=dict)
    direction_findings: dict[str, Any] = Field(default_factory=dict)
    fee_slippage_notes: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)

    @field_validator("sample_quality")
    @classmethod
    def _validate_sample_quality(cls, v: str) -> str:
        allowed = {"unknown", "insufficient", "weak", "moderate", "strong"}
        return v if v in allowed else "unknown"


class ResearchHypothesis(BaseModel):
    """A testable hypothesis derived from research analysis."""

    model_config = ConfigDict(extra="allow")

    hypothesis_id: str = ""
    title: str = ""
    hypothesis: str = ""
    reason: str = ""
    target_engine: str | None = None
    target_strategy_family: str | None = None
    asset_groups: list[str] = Field(default_factory=list)
    symbols: list[str] = Field(default_factory=list)
    timeframes: list[str] = Field(default_factory=list)
    regimes: list[str] = Field(default_factory=list)
    sessions: list[str] = Field(default_factory=list)
    filters: dict[str, Any] = Field(default_factory=dict)
    metrics_to_validate: list[str] = Field(default_factory=list)
    expected_learning: str = ""
    risk: str = "low"
    priority: int = 5
    status: str = "proposed"


class ResearchPlan(BaseModel):
    """A structured research plan with guardrails and safety defaults."""

    model_config = ConfigDict(extra="allow")

    plan_id: str = ""
    created_at: str = Field(default_factory=_now_iso)
    title: str = ""
    objective: str = ""
    hypotheses: list[ResearchHypothesis] = Field(default_factory=list)
    jobs: list[dict[str, Any]] = Field(default_factory=list)
    estimated_scope: str = "small"
    guardrails: dict[str, Any] = Field(default_factory=dict)
    requires_user_approval: bool = True
    can_modify_live_config: bool = False
    can_execute_live_trades: bool = False

    @field_validator("can_modify_live_config")
    @classmethod
    def _validate_no_config_mutation(cls, v: bool) -> bool:
        return False

    @field_validator("can_execute_live_trades")
    @classmethod
    def _validate_no_live_execution(cls, v: bool) -> bool:
        return False


class ResearchRecommendation(BaseModel):
    """An advisory recommendation derived from research evidence.

    All recommendations default to do_not_auto_apply=True and must
    be manually reviewed before any implementation action.
    """

    model_config = ConfigDict(extra="allow")

    recommendation_id: str = ""
    based_on_run_ids: list[str] = Field(default_factory=list)
    recommendation_type: Literal[
        "ADD_FILTER",
        "REMOVE_FILTER",
        "ADJUST_THRESHOLD_CANDIDATE",
        "DISABLE_WEAK_SETUP",
        "PROMOTE_TO_VALIDATION",
        "NEED_MORE_DATA",
        "DO_NOT_CHANGE",
    ] = "DO_NOT_CHANGE"
    target: str = ""
    evidence: str = ""
    expected_impact: str = ""
    confidence: str = "low"
    sample_size: int = 0
    reliability: str = "unknown"
    implementation_notes: str = ""
    do_not_auto_apply: bool = True

    @field_validator("do_not_auto_apply")
    @classmethod
    def _no_auto_apply(cls, v: bool) -> bool:
        return True


class ResearchComparison(BaseModel):
    """Comparison between two research runs."""

    model_config = ConfigDict(extra="allow")

    baseline_run_id: str = ""
    candidate_run_id: str = ""
    what_improved: list[str] = Field(default_factory=list)
    what_worsened: list[str] = Field(default_factory=list)
    statistically_reliable: bool = False
    sample_warning: str | None = None
    recommendation: str = ""
    missing_fields: list[str] = Field(default_factory=list)
