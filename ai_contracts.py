"""Canonical AI agent contracts for advisory trade review packets.

These models are data contracts only. They do not grant execution permission
and must not be used as a replacement for deterministic ATHENA gates.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class _ContextModel(BaseModel):
    model_config = ConfigDict(extra="allow")

    missing_fields: list[str] = Field(default_factory=list)


class AIEngineAContext(_ContextModel):
    score: float | None = None
    max_score: float | None = None
    score_pct: float | None = None
    direction: str | None = None
    regime: Any | None = None
    trend_score: float | None = None
    momentum_score: float | None = None
    adx: float | None = None
    volatility_state: str | None = None
    factor_diagnostics: dict[str, Any] | None = None
    threshold_progress: Any | None = None


class AIEngineBContext(_ContextModel):
    verdict: str | None = None
    confidence: Any | None = None
    direction: str | None = None
    bos: Any | None = None
    choch: Any | None = None
    order_block: Any | None = None
    fvg: Any | None = None
    liquidity_sweep: Any | None = None
    zone_context: Any | None = None
    rr: float | None = None
    style: str | None = None


class AIEngineCContext(_ContextModel):
    decision_state: str | None = None
    combined_conviction: Any | None = None
    a_norm: float | None = None
    b_norm: float | None = None
    blend_case: str | None = None
    watchlist_reason: str | None = None
    reliability_flags: list[str] = Field(default_factory=list)


class AIEngineDContext(_ContextModel):
    setup_type: str | None = None
    gate_result: str | None = None
    executable: bool | None = None
    candidate_status: str | None = None
    quality_score: float | None = None
    quality_grade: str | None = None
    quality_reasons: list[str] = Field(default_factory=list)
    market_state: str | None = None
    session: str | None = None
    execution_tf: str | None = None
    structure_tf: str | None = None
    context_tf: str | None = None
    vp_poc: float | None = None
    vp_vah: float | None = None
    vp_val: float | None = None
    vp_lvn_count: int | None = None
    vp_volume_source: str | None = None
    vp_fidelity: str | None = None
    vp_is_proxy: bool | None = None
    vp_uses_real_trade_buckets: bool | None = None
    profile_anchor_mode: str | None = None
    profile_anchor_start: str | None = None
    profile_anchor_end: str | None = None
    vwap: float | None = None
    absorption_count: int | None = None
    absorption_source: str | None = None
    absorption_fidelity: str | None = None
    absorption_is_proxy: bool | None = None
    cvd_direction: str | None = None
    cvd_slope: float | None = None
    cvd_source: str | None = None
    cvd_bucket_count: int | None = None
    cvd_fidelity: str | None = None
    cvd_is_proxy: bool | None = None
    aaa_complete: bool | None = None
    aggression_source: str | None = None
    aggression_confirmed: bool | None = None
    aggression_uses_real_order_flow: bool | None = None
    rr1: float | None = None
    spread_pips: float | None = None
    fee_guard: dict[str, Any] | None = None
    sl_method: str | None = None
    sl_distance: float | None = None
    rr_ok: bool | None = None
    strict_fabio_pass: bool | None = None
    strict_fabio_reason: str | None = None
    strict_fabio_missing_pillars: list[str] = Field(default_factory=list)
    strict_fabio_pillars: dict[str, Any] | None = None


class AIVisionContext(_ContextModel):
    right_edge_status: str | None = None
    tf_alignment: str | None = None
    confirms_direction: bool | None = None
    style_ratings: dict[str, Any] | None = None
    last_candle_read: Any | None = None
    last_3_5_candles: Any | None = None
    visible_obstacles: Any | None = None
    freshness_status: str | None = None
    chart_timestamp: str | None = None
    latest_candle_ts: str | None = None
    image_hash: str | None = None


class AIRiskContext(_ContextModel):
    rr: float | None = None
    min_rr: float | None = None
    sl: float | None = None
    tp: float | None = None
    entry: float | None = None
    position_size: float | None = None
    spread: float | None = None
    fees: Any | None = None
    slippage: Any | None = None
    guardian_status: str | None = None
    kill_switch_status: str | None = None
    event_risk: Any | None = None
    data_freshness_status: str | None = None


class AICrossEngineContext(BaseModel):
    model_config = ConfigDict(extra="allow")

    cross_engine_alignment: str | None = None
    cross_engine_conflict: str | None = None
    cross_engine_warnings: list[str] = Field(default_factory=list)
    cross_engine_risk_notes: list[str] = Field(default_factory=list)
    cross_engine_location_warning: str | None = None
    cross_engine_rr_warning: str | None = None
    cross_engine_structure_warning: str | None = None
    cross_engine_learning_warning: str | None = None
    native_score_changed_by_cross_engine: bool = False
    native_actionable_changed_by_cross_engine: bool = False
    card_hidden_by_cross_engine: bool = False
    levels_removed_by_cross_engine: bool = False
    consistency_pass: bool = True
    inconsistency_reasons: list[str] = Field(default_factory=list)


class AIDataQualityContext(_ContextModel):
    candle_source: str | None = None
    volume_source: str | None = None
    freshness_status: str | None = None
    stale_flags: list[str] = Field(default_factory=list)
    provider_flags: list[str] = Field(default_factory=list)
    confirmed_only: bool | None = None
    forming_bar_policy: str | None = None


class AISimilarOutcomeContext(_ContextModel):
    examples: list[dict[str, Any]] = Field(default_factory=list)
    aggregate_sample_size: int = 0
    win_rate: float | None = None
    avg_r: float | None = None
    profit_factor: float | None = None
    oos_decay: float | None = None
    reliability: Literal["unavailable", "insufficient", "weak", "moderate", "strong"] = "unavailable"
    warning: str | None = None


class AIMarketIntelligenceContext(BaseModel):
    model_config = ConfigDict(extra="allow")

    schema_version: str = "market_intelligence.v1"
    generated_at: str | None = None
    ttl_seconds: int = 1800
    freshness_status: Literal["fresh", "stale", "partial", "unavailable"] = "unavailable"
    macro_regime: dict[str, Any] = Field(default_factory=dict)
    cross_asset_map: dict[str, Any] = Field(default_factory=dict)
    positioning_extremes: dict[str, Any] = Field(default_factory=dict)
    pair_context: dict[str, Any] = Field(default_factory=dict)
    source_status: dict[str, Any] = Field(default_factory=dict)
    missing_fields: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class VisionTradeRead(BaseModel):
    model_config = ConfigDict(extra="allow")

    schema_version: str = "vision_trade_read.v1"
    right_edge_status: Literal["CONFIRMS", "REVIEW", "POTENTIAL_REVERSAL", "UNKNOWN"] = "UNKNOWN"
    tf_alignment: Literal["ALIGNED", "CONFLICTED", "UNKNOWN"] = "UNKNOWN"
    confirms_direction: bool | None = None
    chart_identity_confidence: Literal["confirmed", "from_request", "unclear"] = "unclear"
    freshness_status: Literal["fresh", "stale", "unknown"] = "unknown"
    allowed_for_execution_context: bool = False
    last_candle_read: dict[str, Any] = Field(default_factory=dict)
    last_3_5_candles: dict[str, Any] = Field(default_factory=dict)
    visible_structure: dict[str, Any] = Field(default_factory=dict)
    style_ratings: dict[str, Any] = Field(default_factory=dict)
    level_suggestions: dict[str, Any] = Field(default_factory=dict)
    memo: str | None = None
    missing_visual_information: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class AIReviewPacket(BaseModel):
    model_config = ConfigDict(extra="allow")

    schema_version: str
    trace_id: str
    symbol: str
    asset_type: str
    direction: str | None = None
    requested_style: str | None = None
    engine_source: str | None = None
    created_at: str
    market_context: dict[str, Any] = Field(default_factory=dict)
    market_intelligence: AIMarketIntelligenceContext | dict[str, Any] = Field(default_factory=AIMarketIntelligenceContext)
    engine_a: AIEngineAContext = Field(default_factory=AIEngineAContext)
    engine_b: AIEngineBContext = Field(default_factory=AIEngineBContext)
    engine_a_native_context: dict[str, Any] = Field(default_factory=dict)
    engine_b_native_context: dict[str, Any] = Field(default_factory=dict)
    cross_engine_context: AICrossEngineContext | dict[str, Any] = Field(default_factory=AICrossEngineContext)
    risk_context: dict[str, Any] = Field(default_factory=dict)
    learning_context: dict[str, Any] = Field(default_factory=dict)
    engine_c: AIEngineCContext = Field(default_factory=AIEngineCContext)
    engine_d: AIEngineDContext = Field(default_factory=AIEngineDContext)
    vision: AIVisionContext = Field(default_factory=AIVisionContext)
    risk: AIRiskContext = Field(default_factory=AIRiskContext)
    data_quality: AIDataQualityContext = Field(default_factory=AIDataQualityContext)
    similar_outcomes: AISimilarOutcomeContext = Field(default_factory=AISimilarOutcomeContext)
    user_question: str | None = None
    context_completeness: dict[str, float] = Field(default_factory=dict)


class AITradeDecision(BaseModel):
    model_config = ConfigDict(extra="allow")

    decision: Literal[
        "NO_TRADE",
        "WATCHLIST",
        "WAIT_FOR_CONFIRMATION",
        "VALID_SETUP",
        "BLOCKED_BY_RISK",
        "DATA_INSUFFICIENT",
    ]
    confidence: float | None = None
    confidence_type: Literal[
        "calibrated_from_outcomes",
        "reasoned_only",
        "insufficient_sample",
    ] = "reasoned_only"
    thesis: str
    market_read: str
    supports: list[str] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)
    confirmation_needed: list[str] = Field(default_factory=list)
    invalidation: str | None = None
    risk_notes: list[str] = Field(default_factory=list)
    historical_evidence_summary: str | None = None
    final_action: str
    ai_may_execute: bool = False
    deterministic_gates_required: bool = True

    @field_validator("ai_may_execute", mode="before")
    @classmethod
    def _force_no_ai_execution(cls, _value: Any) -> bool:
        return False

    @field_validator("deterministic_gates_required", mode="before")
    @classmethod
    def _force_deterministic_gates(cls, _value: Any) -> bool:
        return True


LeeConfirmationVerdict = Literal["CONTEXT_SUPPORTS", "WAIT", "CONTEXT_BLOCKS", "NEED_MORE_DATA"]
LeeConfidence = Literal["low", "medium", "high"]


class LeeSafetyEnvelope(BaseModel):
    model_config = ConfigDict(extra="allow")

    read_only: bool = True
    can_execute: bool = False
    can_modify_thresholds: bool = False
    can_modify_guardian: bool = False
    deterministic_gates_required: bool = True
    execution_blocked: bool = True
    note: str = "Lee is advisory only; Athena deterministic gates remain authoritative."

    @field_validator("read_only", "deterministic_gates_required", "execution_blocked", mode="before")
    @classmethod
    def _force_true(cls, _value: Any) -> bool:
        return True

    @field_validator("can_execute", "can_modify_thresholds", "can_modify_guardian", mode="before")
    @classmethod
    def _force_false(cls, _value: Any) -> bool:
        return False


class LeeExternalContext(BaseModel):
    model_config = ConfigDict(extra="allow")

    schema_version: str = "lee_external_context.v1"
    market_intelligence_freshness: Literal["fresh", "partial", "stale", "unavailable", "unknown"] | str = "unavailable"
    vision_freshness: str | None = None
    source_status: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)


class LeeReasoningDraft(BaseModel):
    model_config = ConfigDict(extra="allow")

    lee_verdict: LeeConfirmationVerdict = "NEED_MORE_DATA"
    confidence: LeeConfidence = "low"
    narrative: str = "Lee needs more verified Athena context before making an advisory read."
    supports: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    missing_data: list[str] = Field(default_factory=list)
    model_used: str = "deterministic_fallback"


class LeeConfirmationResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    schema_version: str = "lee_confirmation.v1"
    generated_at: str
    trace_id: str | None = None
    symbol: str | None = None
    lee_verdict: LeeConfirmationVerdict = "NEED_MORE_DATA"
    display_label: str = "Data gap"
    confidence: LeeConfidence = "low"
    narrative: str = "Lee needs more verified Athena context before making an advisory read."
    supports: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    missing_data: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    safety_flags: list[str] = Field(default_factory=list)
    market_intelligence: dict[str, Any] = Field(default_factory=dict)
    external_context: LeeExternalContext = Field(default_factory=LeeExternalContext)
    selected_signal: dict[str, Any] | None = None
    context_resolution: dict[str, Any] = Field(default_factory=dict)
    model_used: str = "deterministic_fallback"
    advisory_only: bool = True
    execution_allowed: bool = False
    trade_specific_confirmation_allowed: bool = False
    safety: LeeSafetyEnvelope = Field(default_factory=LeeSafetyEnvelope)

    @field_validator("advisory_only", mode="before")
    @classmethod
    def _force_advisory_only(cls, _value: Any) -> bool:
        return True

    @field_validator("execution_allowed", mode="before")
    @classmethod
    def _force_execution_blocked(cls, _value: Any) -> bool:
        return False
