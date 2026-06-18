"""Contracts for versioned AI trade playbooks and structured review output."""

from __future__ import annotations

from typing import Literal, TypedDict

PLAYBOOK_SCHEMA_VERSION = "ai_trade_playbook.v1"
TRADE_SKILL_VERSION = "athena_trade_skill.v1"

ReviewType = Literal["engine_a_chart", "engine_b_chart", "engine_d_scalp"]

TradeSkillDecision = Literal[
    "ENTRY_NOW",
    "WAIT_FOR_PULLBACK",
    "WAIT_FOR_ACCEPTANCE",
    "WATCH_ONLY",
    "NO_TRADE",
    "INVALIDATED",
]

TradeSkillDirection = Literal["LONG", "SHORT", "NEUTRAL"]

EngineDEntryModel = Literal[
    "LVN_REJECTION_CONTINUATION",
    "PULLBACK_TO_VALUE_REJECTION",
    "SWEEP_AND_RECLAIM",
    "BREAK_RETEST_HOLD",
    "FAILED_BREAKOUT_REVERSAL",
    "ABSORPTION_REVERSAL",
    "MOMENTUM_CONTINUATION_AFTER_PULLBACK",
    "NO_TRADE",
]

EngineDMarketState = Literal[
    "trending",
    "balancing",
    "expanding",
    "compressing",
    "choppy",
    "no_trade",
    "transition",
]

_VALID_DECISIONS = frozenset(
    {
        "ENTRY_NOW",
        "WAIT_FOR_PULLBACK",
        "WAIT_FOR_ACCEPTANCE",
        "WATCH_ONLY",
        "NO_TRADE",
        "INVALIDATED",
    }
)

_VALID_DIRECTIONS = frozenset({"LONG", "SHORT", "NEUTRAL"})

_VALID_ENTRY_MODELS = frozenset(
    {
        "LVN_REJECTION_CONTINUATION",
        "PULLBACK_TO_VALUE_REJECTION",
        "SWEEP_AND_RECLAIM",
        "BREAK_RETEST_HOLD",
        "FAILED_BREAKOUT_REVERSAL",
        "ABSORPTION_REVERSAL",
        "MOMENTUM_CONTINUATION_AFTER_PULLBACK",
        "NO_TRADE",
    }
)

_BLOCKING_DECISIONS = frozenset(
    {
        "WAIT_FOR_PULLBACK",
        "WAIT_FOR_ACCEPTANCE",
        "WATCH_ONLY",
        "NO_TRADE",
        "INVALIDATED",
    }
)


class TradeSkillOutput(TypedDict, total=False):
    tradeSkillVersion: str
    reviewType: str
    decision: str
    direction: str
    confidence: int
    marketState: str
    locationAssessment: str
    aggressionAssessment: str
    entryModel: str
    invalidationLevel: float | None
    invalidationReason: str
    entryAllowedNow: bool
    waitReason: str
    noTradeReason: str
    suggestedTradePlan: dict | None
    requiredConfirmation: list[str]
    riskNotes: list[str]
    chartReadSummary: str
    tradeSkillWarnings: list[str]


def extended_output_fields_for(review_type: ReviewType) -> list[str]:
    """Advisory Fabio/Carmine fields — prompt-required but not fail-closed on missing."""
    if review_type == "engine_d_scalp":
        return [
            "aggressionClassification",
            "effortVsResultClassification",
            "trappedTraderAssessment",
            "targetLogic",
            "sessionQuality",
            "sessionConvictionAdjustment",
            "invalidationAssessment",
            "managementPlan",
        ]
    return []


def required_output_fields_for(review_type: ReviewType) -> list[str]:
    base = [
        "tradeSkillVersion",
        "reviewType",
        "decision",
        "direction",
        "confidence",
        "entryAllowedNow",
        "chartReadSummary",
    ]
    if review_type == "engine_d_scalp":
        return base + [
            "marketState",
            "locationAssessment",
            "aggressionAssessment",
            "entryModel",
            "invalidationLevel",
            "invalidationReason",
            "requiredConfirmation",
        ]
    if review_type == "engine_b_chart":
        return base + [
            "locationAssessment",
            "invalidationLevel",
            "invalidationReason",
            "waitReason",
            "noTradeReason",
        ]
    return base + ["waitReason", "noTradeReason"]
