"""Prompt builder for AI chart review."""

from __future__ import annotations

import json
from typing import Any

from ai_playbooks import get_engine_a_playbook, get_engine_b_playbook, render_playbook_prompt_block
from ai_playbooks.trade_skill_normalizer import render_trade_skill_prompt_schema
from ai_review.engine_a_context import build_engine_a_prompt_context, build_engine_b_prompt_context


def _fmt(value: Any) -> str:
    if value is None:
        return "unavailable"
    if isinstance(value, (dict, list)):
        try:
            return json.dumps(value, default=str)
        except Exception:
            return str(value)
    return str(value)


def build_chart_review_prompt(context: dict[str, Any]) -> str:
    atr = context.get("atr") or {}
    geometry = context.get("geometry") or {}
    equity = context.get("equity_session") or {}
    mismatch_warnings = context.get("mismatch_warnings") or []
    engine_a_context = build_engine_a_prompt_context(context)
    engine_b_context = build_engine_b_prompt_context(context)
    engine_a_json = json.dumps({"engineAContext": engine_a_context}, default=str, indent=2)
    engine_b_json = json.dumps({"engineBContext": engine_b_context}, default=str, indent=2)

    playbooks = [get_engine_a_playbook()]
    has_engine_b = bool(engine_b_context and engine_b_context.get("available") is not False)
    if has_engine_b or engine_b_context:
        playbooks.append(get_engine_b_playbook())
    playbook_block = render_playbook_prompt_block(playbooks, compact=True)
    trade_skill_schema = render_trade_skill_prompt_schema("engine_a_chart")

    return f"""You are not only reviewing the chart image. You are validating the chart against the structured Engine A signal supplied below using Athena trade playbooks.

Workflow (required):
1. Follow Engine A playbook: confluence, factor alignment, direction quality, entry timing.
2. If Engine B context is present, follow Engine B playbook: structure, liquidity, zones, invalidation.
3. Decide whether the chart visually confirms Engine A direction (directional validity).
4. Decide whether current entry timing is acceptable — extended/late entries downgrade tradeability even when direction is correct.
5. Output structured trade-skill fields (decision, entryAllowedNow) per schema below. Never grant execution permission.

{playbook_block}

== TRADE SKILL OUTPUT (required top-level fields) ==
{trade_skill_schema}

Return strict JSON only with these top-level keys:
- tradeSkillVersion, reviewType, decision, direction, confidence, entryAllowedNow, waitReason, noTradeReason, chartReadSummary
- locationAssessment (optional), marketState (optional), entryModel (optional), invalidationLevel, invalidationReason
- requiredConfirmation (string[]), riskNotes (string[]), suggestedTradePlan (optional, wait-only)
- aiReviewSummary: {{ humanAction, setupType, overallScore, tradeabilityScore, engineAlignmentScore, visualConfirmationScore, entryQualityScore, riskScore, confidence, finalReason }} (scores 0-100 integers or null)
- engineAVerdictComparison: {{
    engineAProvided, engineABiasValid, engineAPassed, engineADirection, engineAScore, engineAMaxScore,
    engineAThreshold, engineANormalizedScore, engineAActiveFactors,
    chartConfirmsEngineADirection, chartContradictsEngineADirection,
    chartConfirmsEntryTiming, chartContradictsEntryTiming,
    aiAgreesWithEngineA, aiDowngradedEngineA, aiUpgradedEngineA,
    comparisonVerdict, downgradeReasons, upgradeReasons, finalDecision, finalReason
  }}
  comparisonVerdict one of: engine_a_confirmed | engine_a_direction_confirmed_entry_rejected |
  engine_a_contradicted | engine_a_missing | mixed | unknown
- contextCompleteness: {{ score, status, missingRequired, missingOptional, notApplicable, metadata }}
- missingContextDetailed: {{ required: [{{key,label,reason,impact,blocksTrade}}], optional: [...], notApplicable: [...] }}
- visualConfirmation, visualContradiction, atrRrAssessment, entryQuality (strings)
- supportingReasons, risks (string arrays)
- metadata: {{ chartCapturedAt, scanTimestamp, latestCandleTimestamp, chartProvider, engineProvider, providerMismatch }}
- suggestedTradePlan (optional, advisory only): when human_action is wait and a specific level/zone is required before entry, return {{
    schemaVersion: "suggested_trade_plan.v1", armable: true, source: "ai_chart_review", symbol, direction: LONG|SHORT,
    action: WAIT_FOR_LEVEL|WAIT_FOR_ZONE|NO_TRADE, triggerType: ACCEPTANCE_ABOVE|ACCEPTANCE_BELOW|PULLBACK_TO_ZONE|REJECTION_FROM_ZONE|SWEEP_RECLAIM,
    level?, zoneLow?, zoneHigh?, contextTf?, entryTf?, executionTf?, invalidateAbove?, invalidateBelow?, expiresInSeconds?, reason?
  }}
  Omit suggestedTradePlan if no valid numeric level/zone. Never use ENTRY_NOW. This is alert-only — not execution permission.

Also include legacy flat fields for compatibility:
verdict (VALID|CAUTION|INVALID|NO_TRADE), confidence (0-100), setup_type, human_action (take|wait|reject|needs_fresher_data|needs_better_rr),
engine_a_alignment, freshness_assessment, missing_context (string array — see rules below).

Rules:
- Do not approve a trade only because Engine A score is high or passed=true.
- Engine A pass must NOT automatically imply high tradeabilityScore; reduce tradeability when entry is extended/late or visually contradicted.
- If visual contradiction exists, reduce tradeabilityScore and set chartContradictsEngineADirection when appropriate.
- If required context is missing, reduce confidence; do not list not-applicable items as missing.
- COT/carry/funding/OI/intermarket/news/microstructure are non-visual Engine A context.
- Do not reject a trade just because a non-visual factor is not visible on the chart.
- Use the chart image for visual direction and timing validation.
- Use non-visual context to understand why Engine A scored the setup.
- Never change Engine A score or threshold. AI review may validate or downgrade timing only.
- Never claim addonScore is volume. addonScore is the asset add-on only; volumeScore is separate and may be null.
- Do not mark funding/OI missing for non-crypto assets.
- Do not mark carry missing for non-forex assets.
- Do not mark COT missing for assets where addonType is not cot/cot_proxy.
- If nonVisualContext says a driver is unavailable, report it as unavailable, not as bearish/bullish.
- Do NOT put chartCapturedAt, scanTimestamp, or latestCandleTimestamp in missing_context — use metadata only.
- For crypto, equity_session is not applicable — put in notApplicable, not missing.
- ATR freshness: D1 confirmed-only ATR can be 24–48h old — do not flag stale solely on age.
- Candle freshness (crypto 24/7): when engineAContext.candleFreshnessSummary shows policyNote=policy_ok_not_stale or dataFreshnessAllowed=true with only stale_1_bucket on confirmed-only paths, do NOT list H4/D1/H1 as stale in downgradeReasons or freshness_assessment. Reserve stale downgrade for stale_multi_bucket, missing_current_bucket, or dataFreshnessAllowed=false.
- Treat unavailable/null Engine A fields as uncertainty, not zero.
- Do not use NO_TRADE as generic caution. Use WAIT_FOR_PULLBACK / WAIT_FOR_ACCEPTANCE / WATCH_ONLY when direction is acceptable but timing is poor. Use NO_TRADE only with hard invalidation or a concrete noTradeReason.
- This is review-only. Do not issue execution instructions.

== SERVER-TRUSTED engineAContext (JSON) ==
{engine_a_json}

== SERVER-TRUSTED NON-VISUAL ENGINE A CONTEXT ==
nonVisualContext and scoreAttribution are included inside engineAContext above. They are server-trusted diagnostics only; they explain Engine A scoring inputs and do not grant score mutation authority.

== SERVER-TRUSTED engineBContext (JSON) ==
{engine_b_json}

== SYMBOL ==
{context.get("symbol")} {context.get("timeframe")} asset_group: {context.get("asset_group")}

== ATR ==
atr_value: {_fmt(atr.get("atr_value"))} atr_tf: {_fmt(atr.get("atr_tf"))} atr_h4: {_fmt(atr.get("atr_h4"))} atr_d1: {_fmt(atr.get("atr_d1"))}
atr_age_seconds: {_fmt(atr.get("atr_age_seconds"))} atr_freshness: {_fmt(atr.get("atr_freshness_status"))} (max_expected={_fmt(atr.get("max_expected_age_seconds"))}s)

== GEOMETRY ==
entry: {_fmt(geometry.get("candidate_entry"))} sl: {_fmt(geometry.get("stop_loss"))} tp: {_fmt(geometry.get("take_profit"))} rr: {_fmt(geometry.get("rr"))}

== TIMESTAMPS / PROVIDERS ==
scan_timestamp: {_fmt(context.get("scan_timestamp"))} latest_candle_ts: {_fmt(context.get("latest_candle_ts"))}
chart_captured_at: {_fmt(context.get("chart_captured_at"))} (metadata only — not missing context)
engine_provider: {_fmt(context.get("engine_a_provider"))} chart_provider: {_fmt(context.get("chart_provider_hint"))}
equity_session: applied={_fmt(equity.get("applied"))} multiplier={_fmt(equity.get("multiplier"))} reason={_fmt(equity.get("reason"))}
mismatch_warnings: {_fmt(mismatch_warnings)}

Analyse the chart image and return JSON only.
"""
