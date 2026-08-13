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


_VISION_PRIMARY_HEADER = (
    "PRIMARY EVIDENCE: two labelled chart screenshots with indicators drawn.\n"
    "Read IMAGE 1 (structure) and IMAGE 2 (entry/trigger) first. "
    "Playbook and server JSON are supporting facts — do not ignore the images.\n\n"
)

_CHART_REVIEW_A_PREAMBLE_FALLBACK = (
    "You are validating the two chart images against the structured Engine A signal supplied below using Athena trade playbooks.\n\n"
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
    "You are reviewing the two labelled chart images against the structured Engine B (NakedEngine structure/liquidity) signal supplied below using the Engine B trade playbook.\n\n"
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


def _review_images_block(context: dict[str, Any]) -> str:
    images = context.get("review_images") or {}
    if not isinstance(images, dict):
        images = {}
    structure = images.get("structure") or {}
    entry = images.get("entry") or {}
    policy_diagnostic = context.get("review_image_policy_diagnostic") or {}
    if not isinstance(policy_diagnostic, dict):
        policy_diagnostic = {}
    return (
        "== REVIEW IMAGE CONTRACT (server-validated) ==\n"
        f"policy_source={_fmt(policy_diagnostic.get('source'))} "
        f"candidate_vs_reanalysis={_fmt(policy_diagnostic.get('differences'))}\n"
        f"IMAGE 1 role=STRUCTURE timeframe={_fmt(structure.get('timeframe'))} "
        f"captured_at={_fmt(structure.get('capturedAt'))}\n"
        f"IMAGE 2 role=ENTRY_TRIGGER timeframe={_fmt(entry.get('timeframe'))} "
        f"captured_at={_fmt(entry.get('capturedAt'))}\n"
        "Use IMAGE 1 only for structure, directional context, and zone/location review.\n"
        "Use IMAGE 2 for trigger and current entry-timing review. Do not infer entry "
        "timing from IMAGE 1 when the timeframes differ.\n"
        "If a visual role is unavailable or mismatched, report it as not visually "
        "verified; absence is not contradiction.\n"
        "When policy_source=selected_candidate, judge IMAGE 2 against the selected "
        "candidate trigger. Treat a different review-time reanalysis trigger as "
        "provenance, not proof that the candidate image is wrong.\n"
    )


def _engine_b_tf_roles_block(
    style: str,
    engine_b_context: dict[str, Any] | None = None,
) -> str:
    """Render actual server TF roles. No static matrix fallback: missing
    provenance is reported as unavailable, never substituted with a legacy
    hardcoded ladder."""
    style_key = str(style or "intraday").lower()
    if style_key == "auto":
        style_key = "intraday"
    ctx = engine_b_context if isinstance(engine_b_context, dict) else {}
    resolved_roles = {
        "struct": ctx.get("structTf") or "unavailable",
        "zone": ctx.get("zoneTf") or "unavailable",
        "trigger": ctx.get("triggerTf") or "unavailable",
        "atr": ctx.get("atrTf") or "unavailable",
    }
    macro = ctx.get("biasTf") or "unavailable"
    role_source = "server" if any(ctx.get(k) for k in ("structTf", "zoneTf", "triggerTf", "atrTf", "biasTf")) else "unavailable"
    return (
        "== ENGINE B TIMEFRAME ROLES (style-resolved) ==\n"
        f"analyze_style: {style_key}\n"
        f"role_source: {role_source}\n"
        f"struct_tf: {resolved_roles['struct']} | zone_tf: {resolved_roles['zone']} | "
        f"trigger_tf: {resolved_roles['trigger']} | atr_tf: {resolved_roles['atr']}\n"
        f"trigger_tf_expected: {ctx.get('triggerTimeframeExpected', 'unavailable')} | "
        f"trigger_tf_actual: {ctx.get('triggerTimeframeActual', 'unavailable')} | "
        f"trigger_tf_gate_ok: {ctx.get('triggerTimeframeGateOk', 'unavailable')}\n"
        f"macro_swing_tf (bias_tf): {macro}\n"
        "Evaluate zone retest on zone_tf; evaluate entry trigger on trigger_tf.\n"
        "Chart screenshot TF may differ — server-trusted engineBContext gates override visual zone guesses.\n"
        "When locationOk=true and entryOk=true, do not downgrade solely because price is at/near a zone band.\n"
    )


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
    # Only inject Engine B playbook when real structure context is available.
    if isinstance(engine_b_context, dict) and engine_b_context.get("available") is True:
        playbooks.append(get_engine_b_playbook())
    playbook_block = render_playbook_prompt_block(playbooks, compact=True)
    trade_skill_schema = render_trade_skill_prompt_schema("engine_a_chart")
    review_images_block = _review_images_block(context)

    _ac = str(context.get("asset_class") or "").lower()
    _vol_note = ""
    if _ac == "forex":
        _vol_note = " volume_type: tick (not real traded volume)"
    elif _ac == "commodity":
        _vol_note = " volume_type: mixed (may be tick volume)"

    return f"""{_VISION_PRIMARY_HEADER}{_CHART_REVIEW_A_PREAMBLE}

{playbook_block}

{review_images_block}

== TRADE SKILL OUTPUT (required top-level fields) ==
{trade_skill_schema}

Return strict JSON only with these top-level keys:
- tradeSkillVersion, reviewType, decision, direction, confidence, entryAllowedNow, waitReason, noTradeReason, chartReadSummary
- locationAssessment (optional), marketState (optional), entryModel (optional), invalidationLevel, invalidationReason
- requiredConfirmation (string[]), riskNotes (string[]), suggestedTradePlan (optional, wait-only)
- aiReviewSummary: {{ humanAction, setupType, overallScore, tradeabilityScore, engineAlignmentScore, visualConfirmationScore, entryQualityScore, riskScore, confidence, finalReason }} (advisory model-proposed scores 0-100 integers or null; confidence is self-reported and uncalibrated)
- engineAVerdictComparison: {{
    chartConfirmsEngineADirection, chartContradictsEngineADirection,
    chartConfirmsEntryTiming, chartContradictsEntryTiming,
    aiAgreesWithEngineA, aiDowngradedEngineA,
    downgradeReasons, finalDecision, finalReason
  }}
  Do not echo Engine A score, threshold, pass, direction, active factors, or comparisonVerdict.
  The server owns and computes those facts. Never set aiUpgradedEngineA.
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
- Use the two labelled chart images according to REVIEW IMAGE CONTRACT; never use the structure image as proof that a lower-timeframe entry trigger is absent.
- Use non-visual context to understand why Engine A scored the setup.
- Never change Engine A score or threshold. AI review may validate or downgrade timing only.
- Use engineAContext.timeframePolicy for the resolved role ladder, and use entryTimeframe/componentScores for the live entry contribution. D1/H4/H1 remain structural trend layers; never substitute H1 when a lower-TF active entry is supplied.
- activeEntryGate proves whether the active lower-timeframe candle was supplied; it is not directional trigger confirmation. False activeEntryGate.passed, triggerConfirmation.passed, riskGeometry.maxSlPassed, or riskGeometry.rrPassed blocks ENTRY_NOW when the named gate is present.
- When triggerConfirmation.passed=true and IMAGE 2 matches triggerConfirmation.timeframe, do not request the same trigger again unless IMAGE 2 shows concrete contradictory price action after that confirmed trigger.
- Never claim addonScore is volume. addonScore is the asset add-on only; volumeScore is separate and may be null.
- For forex pairs, volumeScore and volumeRatio reflect tick volume, not real traded volume. Do not penalize or downgrade based on volume metrics for forex.
- Do not mark funding/OI missing for non-crypto assets.
- Do not mark carry missing for non-forex assets.
- Do not mark COT missing for assets where addonType is not cot/cot_proxy.
- Do not put engine_b_structure_context / Engine B structure in missing_context when engineBContext.available is false or absent — Engine B structure is an optional overlay on Engine A reviews (use notApplicable, not missing).
- If nonVisualContext says a driver is unavailable, report it as unavailable, not as bearish/bullish.
- Do NOT put chartCapturedAt, scanTimestamp, or latestCandleTimestamp in missing_context — use metadata only.
- For crypto, equity_session is not applicable — put in notApplicable, not missing.
- ATR freshness: D1 confirmed-only ATR can be 24–48h old — do not flag stale solely on age.
- Candle freshness (confirmed-only paths): when engineAContext.candleFreshnessSummary shows policyNote=policy_ok_not_stale or dataFreshnessAllowed=true with only stale_1_bucket on confirmed-only paths, do NOT list H4/D1/H1 as stale in downgradeReasons or freshness_assessment. This applies across crypto, forex, stocks, indices, commodities, and ETFs. Reserve stale downgrade for stale_multi_bucket, missing_current_bucket, or dataFreshnessAllowed=false.
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
role: STRUCTURE (IMAGE 1)
rendered_layers: {_fmt_list(rendered_layers)}
visible_candle_count: {_fmt(chart_snapshot.get("visibleCandleCount"))}
visible_range: {_fmt(chart_snapshot.get("visibleRange"))}
chart_data_status: {_fmt(chart_snapshot.get("chartDataStatus"))} latest_chart_candle: {_fmt(chart_snapshot.get("lastCandleTs"))} age_seconds: {_fmt(chart_snapshot.get("lastCandleAgeSec"))}
engine_b_overlay_status: {_fmt(chart_snapshot.get("engineBOverlayStatus"))}
engine_b_overlay_count: {_fmt(chart_snapshot.get("engineBOverlayCount"))}
indicator_layer_states: {_fmt(chart_snapshot.get("indicatorLayerStates"))}

Analyse both labelled chart images and return JSON only.
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
    analyze_style = str(context.get("analyze_style") or "intraday")
    tf_roles_block = _engine_b_tf_roles_block(analyze_style, engine_b_context)
    review_images_block = _review_images_block(context)

    return f"""{_VISION_PRIMARY_HEADER}{_CHART_REVIEW_B_PREAMBLE}
1. Follow Engine B playbook: structure, liquidity, zones, invalidation.
2. Decide whether the chart visually confirms Engine B direction.
3. Decide whether current entry timing is acceptable at the nearest zone/structure.
4. Output structured trade-skill fields per schema below. Never grant execution permission.

{playbook_block}

{review_images_block}

{tf_roles_block}
== TRADE SKILL OUTPUT (required top-level fields) ==
{trade_skill_schema}

Return strict JSON only with these top-level keys:
- tradeSkillVersion, reviewType, decision, direction, confidence, entryAllowedNow, waitReason, noTradeReason, chartReadSummary
- locationAssessment, invalidationLevel, invalidationReason
- requiredConfirmation (string[]), riskNotes (string[])
- aiReviewSummary: {{ humanAction, setupType, overallScore, tradeabilityScore, engineAlignmentScore, visualConfirmationScore, entryQualityScore, riskScore, confidence, finalReason }} (advisory model-proposed scores; confidence is self-reported and uncalibrated)
- engineBVerdictComparison: {{
    chartConfirmsEngineBDirection, chartContradictsEngineBDirection,
    chartConfirmsEntryTiming, chartContradictsEntryTiming,
    aiAgreesWithEngineB, aiDowngradedEngineB,
    downgradeReasons, finalDecision, finalReason
  }}
  Do not echo Engine B score, threshold, pass, direction, structural verdict, or comparisonVerdict.
  The server owns and computes those facts. Never set aiUpgradedEngineB.
- contextCompleteness, missingContextDetailed, visualConfirmation, visualContradiction, atrRrAssessment, entryQuality
- supportingReasons, risks (string arrays)
- metadata: {{ chartCapturedAt, scanTimestamp, latestCandleTimestamp, chartProvider, engineProvider, providerMismatch }}

Also include legacy flat fields:
verdict (VALID|CAUTION|INVALID|NO_TRADE), confidence (0-100), setup_type, human_action (take|wait|reject|needs_fresher_data|needs_better_rr),
engine_b_alignment, freshness_assessment, missing_context (string array).

Rules:
- Do not approve a trade only because Engine B score is high or passed=true.
- Never change Engine B score or threshold. AI review may validate or downgrade timing only.
- Use IMAGE 1 for structure/direction and IMAGE 2 for trigger/entry timing. Never use IMAGE 1 as evidence that a lower-timeframe trigger is absent.
- Engine B overlays on the chart (zones, BOS, FVG) are advisory visual context — server-trusted structure fields in engineBContext are authoritative.
- Engine B is zone-retest: when locationOk=true and entryOk=true, retest at the active zone is valid — do not reflex-reject as inside resistance/support.
- Judge zones on zone_tf and triggers on trigger_tf (see ENGINE B TIMEFRAME ROLES); chart TF may differ from zone_tf.
- Use the server-resolved timeframe roles and require triggerTimeframeGateOk=true when the lower-TF trigger gate is present; never replace M15/M30 with H1 evidence.
- When triggerTimeframeGateOk=true and the confirmed trigger is stamped on the same timeframe as IMAGE 2, do not request that same trigger merely because IMAGE 1 lacks it. A downgrade must cite concrete contradictory price action visible on IMAGE 2.
- Treat structureOk, locationOk, entryOk, spaceGateOk, rrOk, maxSlPassed, and executionLevelsValid as deterministic gates. AI cannot override a false gate.
- Cite gateScore/gateMaxPossible separately from quality. Headline quality is qualityPct / qualityPctNet (0-100). qualityScore is earned points, not a percent. Never use gatePct as a quality blend.
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
chart_data_status: {_fmt(chart_snapshot.get("chartDataStatus"))} latest_chart_candle: {_fmt(chart_snapshot.get("lastCandleTs"))} age_seconds: {_fmt(chart_snapshot.get("lastCandleAgeSec"))}
engine_b_overlay_status: {_fmt(chart_snapshot.get("engineBOverlayStatus"))}
engine_b_overlay_count: {_fmt(chart_snapshot.get("engineBOverlayCount"))}

Analyse the chart image and return JSON only.
"""
