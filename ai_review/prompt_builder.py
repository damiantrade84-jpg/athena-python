"""Prompt builder for AI chart review."""

from __future__ import annotations

import json
from typing import Any

from ai_playbooks import get_engine_a_playbook, get_engine_b_playbook, render_playbook_prompt_block
from ai_playbooks.trade_skill_normalizer import render_trade_skill_prompt_schema
from ai_review.engine_a_context import build_engine_a_prompt_context, build_engine_b_prompt_context
from ai_review.ase_context import render_ase_prompt_block
from ai_review.macro_context import render_macro_prompt_block
from prompt_store import load_prompt


_CHART_REVIEW_A_PREAMBLE_FALLBACK = (
    "You are not only reviewing the chart image. You are validating the chart against the structured Engine A signal supplied below using Athena trade playbooks.\n\n"
    "Workflow (required):\n"
    "1. Follow Engine A playbook: confluence, factor alignment, direction quality, entry timing.\n"
    "2. If Engine B context is present, follow Engine B playbook: structure, liquidity, zones, invalidation.\n"
    "3. Decide whether the chart visually confirms Engine A direction (directional validity).\n"
    "4. Decide whether current entry timing is acceptable. Acceptable timing is common: a confirmed BOS with acceptance/retest, a pullback to structure, or a breakout retest pass timing. Only mark timing poor on concrete evidence (price measurably extended from value/structure in ATR terms with no pullback, exhaustion, or RR degraded) — not as a reflex because Engine A passed. Genuinely extended/late entries do downgrade tradeability even when direction is correct.\n"
    "5. Output structured trade-skill fields (decision, entryAllowedNow) per schema below. Never grant execution permission.\n"
)
_CHART_REVIEW_A_PREAMBLE, _CRA_SOURCE, _CRA_HASH = load_prompt(
    "chart_review_engine_a_preamble",
    fallback=_CHART_REVIEW_A_PREAMBLE_FALLBACK,
)

_CHART_REVIEW_B_PREAMBLE_FALLBACK = (
    "You are reviewing the chart image against the structured Engine B (NakedEngine structure/liquidity) signal supplied below using the Engine B trade playbook.\n\n"
    "Workflow (required):\n"
)
_CHART_REVIEW_B_PREAMBLE, _CRB_SOURCE, _CRB_HASH = load_prompt(
    "chart_review_engine_b_preamble",
    fallback=_CHART_REVIEW_B_PREAMBLE_FALLBACK,
)


def _fmt(value: Any) -> str:
    if value is None:
        return "unavailable"
    if isinstance(value, (dict, list)):
        try:
            return json.dumps(value, default=str)
        except Exception:
            return str(value)
    return str(value)


def _fmt_list(value: Any) -> str:
    if not isinstance(value, list) or not value:
        return "unavailable"
    return ", ".join(str(item) for item in value)


def build_chart_review_prompt(context: dict[str, Any]) -> str:
    if str(context.get("primary_engine") or "A").upper() == "B":
        return _build_engine_b_chart_review_prompt(context)
    return _build_engine_a_chart_review_prompt(context)


def _build_engine_a_chart_review_prompt(context: dict[str, Any]) -> str:
    atr = context.get("atr") or {}
    geometry = context.get("geometry") or {}
    equity = context.get("equity_session") or {}
    mismatch_warnings = context.get("mismatch_warnings") or []
    review_style = context.get("review_style_diagnostic") or {}
    indicator_parity = context.get("indicator_parity") or {}
    chart_snapshot = context.get("chart_snapshot") or {}
    if not isinstance(chart_snapshot, dict):
        chart_snapshot = {}
    rendered_layers = chart_snapshot.get("renderedLayers")
    if not rendered_layers:
        rendered_layers = context.get("screenshot_overlays") or []
    engine_a_context = build_engine_a_prompt_context(context)
    engine_b_context = build_engine_b_prompt_context(context)
    engine_a_json = json.dumps({"engineAContext": engine_a_context}, default=str, indent=2)
    engine_b_json = json.dumps({"engineBContext": engine_b_context}, default=str, indent=2)
    ase_block = render_ase_prompt_block(context.get("aseSignal") or context.get("ase_signal"))
    macro_block = render_macro_prompt_block(context.get("symbol"), context.get("asset_class"))

    playbooks = [get_engine_a_playbook()]
    has_engine_b = bool(engine_b_context and engine_b_context.get("available") is not False)
    if has_engine_b or engine_b_context:
        playbooks.append(get_engine_b_playbook())
    playbook_block = render_playbook_prompt_block(playbooks, compact=True)
    trade_skill_schema = render_trade_skill_prompt_schema("engine_a_chart")

    _ac = str(context.get("asset_class") or "").lower()
    _vol_note = ""
    if _ac == "forex":
        _vol_note = " volume_type: tick (not real traded volume)"
    elif _ac == "commodity":
        _vol_note = " volume_type: mixed (may be tick volume)"

    return f"""{_CHART_REVIEW_A_PREAMBLE}

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
- For forex pairs, volumeScore and volumeRatio reflect tick volume, not real traded volume. Do not penalize or downgrade based on volume metrics for forex.
- Do not mark funding/OI missing for non-crypto assets.
- Do not mark carry missing for non-forex assets.
- Do not mark COT missing for assets where addonType is not cot/cot_proxy.
- If nonVisualContext says a driver is unavailable, report it as unavailable, not as bearish/bullish.
- Do NOT put chartCapturedAt, scanTimestamp, or latestCandleTimestamp in missing_context — use metadata only.
- For crypto, equity_session is not applicable — put in notApplicable, not missing.
- ATR freshness: D1 confirmed-only ATR can be 24–48h old — do not flag stale solely on age.
- Candle freshness (crypto 24/7): when engineAContext.candleFreshnessSummary shows policyNote=policy_ok_not_stale or dataFreshnessAllowed=true with only stale_1_bucket on confirmed-only paths, do NOT list H4/D1/H1 as stale in downgradeReasons or freshness_assessment. Reserve stale downgrade for stale_multi_bucket, missing_current_bucket, or dataFreshnessAllowed=false.
- Treat unavailable/null Engine A fields as uncertainty, not zero.
- Chart indicator series are computed on confirmed bars only, and the rightmost candle on the image may still be forming. Do not treat the forming candle as a confirmed close, BOS, rejection, or breakout — base confirmed-close judgments on closed bars.
- Do not use NO_TRADE as generic caution. Use WAIT_FOR_PULLBACK / WAIT_FOR_ACCEPTANCE / WATCH_ONLY when direction is acceptable but timing is poor. Use NO_TRADE only with hard invalidation or a concrete noTradeReason.
- This is review-only. Do not issue execution instructions.

== SERVER-TRUSTED engineAContext (JSON) ==
{engine_a_json}

== SERVER-TRUSTED NON-VISUAL ENGINE A CONTEXT ==
nonVisualContext and scoreAttribution are included inside engineAContext above. They are server-trusted diagnostics only; they explain Engine A scoring inputs and do not grant score mutation authority.

== SERVER-TRUSTED engineBContext (JSON) ==
{engine_b_json}

{ase_block}
{macro_block}
== SYMBOL ==
{context.get("symbol")} {context.get("timeframe")} asset_group: {context.get("asset_group")}{_vol_note}
analyze_style: {_fmt(context.get("analyze_style"))} scoring_tfs: {_fmt(context.get("scoring_timeframes"))} momentum_tf: {_fmt(context.get("momentum_timeframe"))} regime_tf: {_fmt(context.get("regime_timeframe"))} execution_tf: {_fmt(context.get("execution_timeframe"))}

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

== REVIEW STYLE / INDICATOR PARITY (advisory, server-trusted) ==
review_analyze_style: {_fmt(review_style.get("review_analyze_style"))} candidate_signal_style: {_fmt(review_style.get("candidate_signal_style"))} style_matches_candidate: {_fmt(review_style.get("style_matches_candidate"))}
review_style_note: {_fmt(review_style.get("note"))}
indicator_parity: chart_tf={_fmt(indicator_parity.get("chart_timeframe"))} engine_a_indicator_tf={_fmt(indicator_parity.get("engine_a_indicator_timeframe"))} status={_fmt(indicator_parity.get("status"))} mismatches={_fmt(indicator_parity.get("mismatches"))}
(Chart indicators are computed on the visible timeframe; Engine A trend EMAs are H4. When status is not_comparable_timeframe or values_differ, do not read the chart's drawn EMA/ATR/ADX as Engine A's values.)
(VWAP anchors differ: the chart-drawn VWAP line is UTC-day session-anchored on intraday timeframes, while engineAContext vwapDistanceAtr/vwapExtended use a multi-day anchored H4 VWAP. Do not treat the chart VWAP line as the basis of the engine's VWAP-extension diagnostics.)

== CHART CAPTURE METADATA ==
rendered_layers: {_fmt_list(rendered_layers)}
visible_candle_count: {_fmt(chart_snapshot.get("visibleCandleCount"))}
visible_range: {_fmt(chart_snapshot.get("visibleRange"))}
engine_b_overlay_status: {_fmt(chart_snapshot.get("engineBOverlayStatus"))}
engine_b_overlay_count: {_fmt(chart_snapshot.get("engineBOverlayCount"))}
indicator_layer_states: {_fmt(chart_snapshot.get("indicatorLayerStates"))}

Analyse the chart image and return JSON only.
"""


def _build_engine_b_chart_review_prompt(context: dict[str, Any]) -> str:
    atr = context.get("atr") or {}
    geometry = context.get("geometry") or {}
    mismatch_warnings = context.get("mismatch_warnings") or []
    chart_snapshot = context.get("chart_snapshot") or {}
    if not isinstance(chart_snapshot, dict):
        chart_snapshot = {}
    rendered_layers = chart_snapshot.get("renderedLayers")
    if not rendered_layers:
        rendered_layers = context.get("screenshot_overlays") or []
    engine_b_context = build_engine_b_prompt_context(context)
    engine_b_json = json.dumps({"engineBContext": engine_b_context}, default=str, indent=2)
    playbook_block = render_playbook_prompt_block([get_engine_b_playbook()], compact=True)
    trade_skill_schema = render_trade_skill_prompt_schema("engine_b_chart")
    macro_block = render_macro_prompt_block(context.get("symbol"), context.get("asset_class"))

    return f"""{_CHART_REVIEW_B_PREAMBLE}
1. Follow Engine B playbook: structure, liquidity, zones, invalidation.
2. Decide whether the chart visually confirms Engine B direction.
3. Decide whether current entry timing is acceptable at the nearest zone/structure.
4. Output structured trade-skill fields per schema below. Never grant execution permission.

{playbook_block}

== TRADE SKILL OUTPUT (required top-level fields) ==
{trade_skill_schema}

Return strict JSON only with these top-level keys:
- tradeSkillVersion, reviewType, decision, direction, confidence, entryAllowedNow, waitReason, noTradeReason, chartReadSummary
- locationAssessment, invalidationLevel, invalidationReason
- requiredConfirmation (string[]), riskNotes (string[])
- aiReviewSummary: {{ humanAction, setupType, overallScore, tradeabilityScore, engineAlignmentScore, visualConfirmationScore, entryQualityScore, riskScore, confidence, finalReason }}
- engineBVerdictComparison: {{
    engineBProvided, engineBBiasValid, engineBPassed, engineBDirection, engineBScore, engineBMaxScore,
    engineBThreshold, engineBNormalizedScore, engineBStructuralVerdict,
    chartConfirmsEngineBDirection, chartContradictsEngineBDirection,
    chartConfirmsEntryTiming, chartContradictsEntryTiming,
    aiAgreesWithEngineB, aiDowngradedEngineB, aiUpgradedEngineB,
    comparisonVerdict, downgradeReasons, upgradeReasons, finalDecision, finalReason
  }}
  comparisonVerdict one of: engine_b_confirmed | engine_b_direction_confirmed_entry_rejected |
  engine_b_contradicted | engine_b_missing | mixed | unknown
- contextCompleteness, missingContextDetailed, visualConfirmation, visualContradiction, atrRrAssessment, entryQuality
- supportingReasons, risks (string arrays)
- metadata: {{ chartCapturedAt, scanTimestamp, latestCandleTimestamp, chartProvider, engineProvider, providerMismatch }}

Also include legacy flat fields:
verdict (VALID|CAUTION|INVALID|NO_TRADE), confidence (0-100), setup_type, human_action (take|wait|reject|needs_fresher_data|needs_better_rr),
engine_b_alignment, freshness_assessment, missing_context (string array).

Rules:
- Do not approve a trade only because Engine B score is high or passed=true.
- Never change Engine B score or threshold. AI review may validate or downgrade timing only.
- Use the chart image for visual direction and timing validation.
- Engine B overlays on the chart (zones, BOS, FVG) are advisory visual context — server-trusted structure fields in engineBContext are authoritative.
- This is review-only. Do not issue execution instructions.

== SERVER-TRUSTED engineBContext (JSON) ==
{engine_b_json}

{macro_block}
== SYMBOL ==
{context.get("symbol")} {context.get("timeframe")} asset_group: {context.get("asset_group")}
analyze_style: {_fmt(context.get("analyze_style"))}

== ATR ==
atr_value: {_fmt(atr.get("atr_value"))} atr_tf: {_fmt(atr.get("atr_tf"))} atr_source: {_fmt(atr.get("atr_source"))}

== GEOMETRY ==
entry: {_fmt(geometry.get("candidate_entry"))} sl: {_fmt(geometry.get("stop_loss"))} tp: {_fmt(geometry.get("take_profit"))} rr: {_fmt(geometry.get("rr"))}

== TIMESTAMPS / PROVIDERS ==
scan_timestamp: {_fmt(context.get("scan_timestamp"))} chart_captured_at: {_fmt(context.get("chart_captured_at"))}
engine_provider: {_fmt(context.get("engine_b_provider"))} chart_provider: {_fmt(context.get("chart_provider_hint"))}
mismatch_warnings: {_fmt(mismatch_warnings)}

== CHART CAPTURE METADATA ==
rendered_layers: {_fmt_list(rendered_layers)}
visible_candle_count: {_fmt(chart_snapshot.get("visibleCandleCount"))}
engine_b_overlay_status: {_fmt(chart_snapshot.get("engineBOverlayStatus"))}
engine_b_overlay_count: {_fmt(chart_snapshot.get("engineBOverlayCount"))}

Analyse the chart image and return JSON only.
"""
