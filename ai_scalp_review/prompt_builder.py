"""Prompt builder for Engine D scalp chart review."""

from __future__ import annotations

import json
from typing import Any

from ai_playbooks import get_engine_d_scalp_playbook, render_playbook_prompt_block
from ai_playbooks.trade_skill_normalizer import render_trade_skill_prompt_schema
from ai_scalp_review.engine_d_context import build_engine_d_prompt_context


def _fmt(value: Any) -> str:
    if value is None:
        return "unavailable"
    if isinstance(value, (dict, list)):
        try:
            return json.dumps(value, default=str)
        except Exception:
            return str(value)
    return str(value)


def build_scalp_chart_review_prompt(context: dict[str, Any]) -> str:
    engine_d_context = build_engine_d_prompt_context(context)
    engine_d_json = json.dumps({"engineDContext": engine_d_context}, default=str, indent=2)
    source = context.get("sourceContract") or {}
    location = context.get("marketLocation") or {}
    setup = context.get("scalpSetup") or {}

    playbook_block = render_playbook_prompt_block([get_engine_d_scalp_playbook()], compact=True)
    trade_skill_schema = render_trade_skill_prompt_schema("engine_d_scalp")

    return f"""You are reviewing a scalp chart against the server-trusted Engine D setup below using the Engine D naked-chart scalp playbook.

Workflow (required — follow this exact order):
Market State -> Location -> Aggression -> Entry Model -> Invalidation -> Decision

1. Market State: classify as trending, balancing, expanding, compressing, choppy/no_trade, or transition.
2. Location: assess value area/POC/VAH/VAL, HVN/LVN, supply/demand, session H/L, swings, liquidity, premium/discount, chase risk.
3. Aggression: assess buying/selling aggression, absorption, exhaustion, delta/volume imbalance, displacement, wick rejection, sweep/reclaim.
4. Entry Model: choose one allowed model or NO_TRADE.
5. Invalidation: exact level/zone, what proves setup wrong, SL validity, RR acceptability.
6. Decision: ENTRY_NOW | WAIT_FOR_PULLBACK | WAIT_FOR_ACCEPTANCE | WATCH_ONLY | NO_TRADE | INVALIDATED.

Timeframe rules: M5 is default context chart; M1 is execution zoom only, not primary context.
Engine D grade/pass does NOT auto-imply trade. Never grant execution permission.

{playbook_block}

== TRADE SKILL OUTPUT (required top-level fields) ==
{trade_skill_schema}

Return strict JSON only with these top-level keys:
- tradeSkillVersion, reviewType, decision, direction, confidence, entryAllowedNow
- marketState, locationAssessment, aggressionAssessment, entryModel
- invalidationLevel, invalidationReason, waitReason, noTradeReason, chartReadSummary
- requiredConfirmation (string[]), riskNotes (string[]), suggestedTradePlan (optional, wait-only)
- aiReviewSummary: {{ provider, model, humanAction, setupType, overallScore, tradeabilityScore, visualConfirmationScore, entryQualityScore, riskScore, sourceQualityScore, confidence, finalReason }} (scores 0-100 integers or null)
- scalpVerdictComparison: {{
    setupProvided, setupDirection, setupGrade, setupScore, setupPassed,
    chartConfirmsDirection, chartContradictsDirection,
    chartConfirmsEntryTiming, chartContradictsEntryTiming,
    sourceQualitySupportsReview, aiDowngradedSetup,
    comparisonVerdict, downgradeReasons, finalDecision, finalReason
  }}
  comparisonVerdict one of: setup_confirmed | direction_confirmed_entry_rejected | setup_contradicted | source_quality_insufficient | setup_missing | mixed | unknown
- contextCompleteness: {{ score, status, missingRequired, missingOptional, notApplicable, metadata }}
- visualConfirmation, visualContradiction, entryQuality, sourceQualityAssessment (strings)
- supportingReasons, risks (string arrays)
- metadata: {{ chartCapturedAt, latestCandleTimestamp, candleSource, orderflowSource, vpSource }}
- suggestedTradePlan (optional, advisory only): when human_action is wait and a level/zone trigger applies, return {{
    schemaVersion: "suggested_trade_plan.v1", armable: true, source: "ai_scalp_chart_review", symbol, direction: LONG|SHORT,
    action: WAIT_FOR_LEVEL|WAIT_FOR_ZONE|NO_TRADE, triggerType: ACCEPTANCE_ABOVE|ACCEPTANCE_BELOW|PULLBACK_TO_ZONE|REJECTION_FROM_ZONE|SWEEP_RECLAIM,
    level?, zoneLow?, zoneHigh?, contextTf?, entryTf?, executionTf?, invalidateAbove?, invalidateBelow?, expiresInSeconds?, reason?
  }}
  Omit if no valid numeric level/zone. Never ENTRY_NOW. Alert-only — not execution permission.

Also include legacy flat fields:
verdict (VALID|CAUTION|INVALID|NO_TRADE), confidence (0-100), setup_type, human_action (take|wait|reject|needs_fresher_data|needs_better_rr),
missing_context (string array).

Rules:
- Do not approve a trade only because aiGrade is A/B or executable=true.
- Reduce tradeabilityScore when entry is at poor location or source contract is proxy/unverified.
- Reduce sourceQualityScore when strictOrderflowSourcePass is false or unavailableReasons exist.
- This is review-only. Do not issue execution instructions.

== SERVER-TRUSTED engineDContext (JSON) ==
{engine_d_json}

== SYMBOL / TF ==
{context.get("symbol")} execution_tf: {_fmt(context.get("execution_tf"))} chart_tf: {_fmt(context.get("timeframe"))}

== SETUP ==
direction: {_fmt(context.get("direction"))} grade: {_fmt(context.get("ai_grade"))} score: {_fmt(context.get("ai_score"))}
entry: {_fmt(setup.get("entry"))} sl: {_fmt(setup.get("stopLoss"))} tp1: {_fmt(setup.get("tp1"))} rr1: {_fmt(setup.get("rr1"))}

== LOCATION ==
label: {_fmt(location.get("locationLabel"))} poc: {_fmt(location.get("poc"))} vah: {_fmt(location.get("vah"))} val: {_fmt(location.get("val"))}

== SOURCE CONTRACT ==
orderflow_real: {_fmt(source.get("orderflowSourceIsReal"))} strict_orderflow_pass: {_fmt(source.get("strictOrderflowSourcePass"))}
unavailable: {_fmt(source.get("unavailableReasons"))}

Analyse the chart image and return JSON only.
"""
