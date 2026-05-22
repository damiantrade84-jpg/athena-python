"""Prompt builder for Engine D scalp chart review."""

from __future__ import annotations

import json
from typing import Any

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

    return f"""You are reviewing a scalp chart against the server-trusted Engine D setup below.

Workflow (required):
1. Decide whether the chart visually confirms the setup direction.
2. Decide whether entry timing is acceptable (POC/VAH/VAL/LVN location, not extended/chasing).
3. Decide whether source contract quality supports this review (real orderflow/VP vs proxy).
4. Decide human action: trade | wait | reject | watch. Engine D grade/pass does NOT auto-imply trade.

Return strict JSON only with these top-level keys:
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
