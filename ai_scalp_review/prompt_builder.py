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
    client_snapshot = context.get("clientChartSnapshot")
    engine_d_json = json.dumps(
        {"engineDContext": engine_d_context, "clientChartSnapshot": client_snapshot},
        default=str,
        indent=2,
    )
    source = context.get("sourceContract") or {}
    location = context.get("marketLocation") or {}
    setup = context.get("scalpSetup") or {}

    playbook_block = render_playbook_prompt_block([get_engine_d_scalp_playbook()], compact=True)
    trade_skill_schema = render_trade_skill_prompt_schema("engine_d_scalp")

    return f"""You are reviewing a scalp chart against the server-trusted Engine D setup below using the Engine D naked-chart scalp playbook.

Workflow (required — follow this exact order):
Session Context -> Market State -> Effort vs Result -> Location -> Trapped Traders -> Entry Model -> Target -> Invalidation -> Management -> Decision

0. Session Context: use server-trusted engineDContext.sessionContext (currentSession, deliveryWindow, preferredModel). Do NOT guess session from screenshot.
1. Market State: classify as trending, balancing, expanding, compressing, choppy/no_trade, or transition.
2. Effort vs Result: classify effortVsResultClassification and aggressionClassification BEFORE entry model.
3. Location: assess value area/POC/VAH/VAL, HVN/LVN, supply/demand, session H/L, swings, liquidity, chase risk.
4. Trapped Traders: score trappedTraderAssessment (trappedSide, trapTrigger, squeezeFuelScore).
5. Entry Model: choose Model A (NY trend squeeze) or Model B (London mean reversion) per sessionContext.preferredModel.
6. Target: populate targetLogic — POC magnet for mean-reversion; structural liquidity for continuation.
7. Invalidation: structural stop geometry — reject ATR-only stops; populate invalidationAssessment.
8. Management: for ENTRY_NOW, populate managementPlan (BE trigger, scale-out, invalidation exit).
9. Decision: ENTRY_NOW | WAIT_FOR_PULLBACK | WAIT_FOR_ACCEPTANCE | WATCH_ONLY | NO_TRADE | INVALIDATED.

Timeframe rules: M5 is default context chart; M1 is execution zoom only, not primary context.
Engine D grade/pass does NOT auto-imply trade. Never grant execution permission.

{playbook_block}

== TRADE SKILL OUTPUT (required top-level fields) ==
{trade_skill_schema}

Return strict JSON only with these top-level keys:
- tradeSkillVersion, reviewType, decision, direction, confidence, entryAllowedNow
- marketState, locationAssessment, aggressionAssessment, entryModel
- aggressionClassification: ABSORPTION | INITIATIVE_AGGRESSION | EXHAUSTION | NO_CLEAR_FLOW
- effortVsResultClassification: HIGH_EFFORT_NO_RESULT | HIGH_EFFORT_HIGH_RESULT | LOW_EFFORT_STALLED_RESULT | LOW_EFFORT_HIGH_RESULT | UNKNOWN
- trappedTraderAssessment: {{ trappedSide: LONGS|SHORTS|NONE|UNKNOWN, trapTrigger, squeezeFuelScore: 0-100, explanation }}
- targetLogic: {{ primaryTargetType: POC|VAH|VAL|LVN|PRIOR_HIGH_LOW|LIQUIDITY_POOL|STRUCTURAL_RESISTANCE_SUPPORT|NONE, primaryTargetPrice, targetJustification, structuralRR }}
- sessionQuality: CLEAN_DELIVERY | ACCEPTABLE | CHOP_RISK | NO_TRADE_WINDOW
- sessionConvictionAdjustment: UPGRADE | NEUTRAL | DOWNGRADE | HARD_REJECT
- invalidationLevel, invalidationReason
- invalidationAssessment: {{ structuralInvalidationLevel, proposedStopLevel, stopPlacementValid, stopProblem, expectedBehavior }}
- managementPlan (required when decision=ENTRY_NOW): {{ moveToBETrigger, scaleOutPlan: {{ firstScalePercent, firstScaleTarget, runnerTarget }}, invalidationExit, maxTimeInTradeReasoning }}
- waitReason, noTradeReason, chartReadSummary
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
- Do not use NO_TRADE as generic caution. Use WAIT_FOR_PULLBACK / WAIT_FOR_ACCEPTANCE / WATCH_ONLY when direction is acceptable but timing is poor. Use NO_TRADE only with hard invalidation or a concrete noTradeReason.
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

== SCALP WORKBENCH EXECUTION CHART (Fabio / Carmine) ==
You are reviewing the Scalp Workbench execution chart screenshot and optional clientChartSnapshot.
Only approve ENTRY_NOW if the displayed chart and structured snapshot show ALL of:
1. valid macro location (not middle-of-value unless mean-reversion model),
2. valid liquidity event or LVN/VA boundary interaction,
3. valid aggression classification (absorption, initiative, or exhaustion — not unclear flow),
4. clean invalidation level visible on chart,
5. non-extended entry (not chasing after the move),
6. source quality sufficient for the setup type.

Reject or WAIT if:
- price is in middle of value without a valid balance mean-reversion model,
- no liquidity sweep/reclaim when reversal model requires it,
- no LVN pullback when continuation model requires it,
- aggression is unclear or proxy-only for a flow-dependent setup,
- entry is chasing after displacement,
- stop location is structurally invalid,
- source quality is too weak for the trade type.

Chart layers to reference: candles, auction POC/VAH/VAL, liquidity PDH/PDL/session/sweep,
Engine B nearby zones, order-flow markers, candidate entry/SL/TP, thesis badge, advisory warnings.

== FABIO/CARMINE SESSION + MODEL SWITCH (use server sessionContext) ==
- Read engineDContext.sessionContext and sessionConvictionHint — authoritative for session/time.
- Model A (NY_TREND_SQUEEZE): NY open delivery, vol expanding, broke balance, LVN/reclaim pullback, trapped counter-trend, initiative resumes. Do NOT chase first breakout candle.
- Model B (LONDON_MEAN_REVERSION): lower vol, range/balance, sweep VAH/VAL/session extreme, failed breakout, absorption, reclaim range. Target POC; reject middle-of-value.

== EFFORT VS RESULT DECISION TABLE (classify before entry model) ==
| Classification | Meaning | Action |
| HIGH_EFFORT_NO_RESULT | ABSORPTION | fade/reversal only at qualified macro level |
| HIGH_EFFORT_HIGH_RESULT | INITIATIVE_AGGRESSION | continuation after pullback/retest — do NOT chase |
| LOW_EFFORT_STALLED_RESULT | EXHAUSTION | mean-reversion at VAH/VAL/liquidity extreme |
| LOW_EFFORT_HIGH_RESULT | thin/liquidity vacuum | caution; require reclaim/acceptance |

== TRAPPED TRADER / SQUEEZE FUEL ==
- Failed breakdown + reclaim = trapped SHORTS. Failed breakout + reclaim lower = trapped LONGS.
- Highest conviction: aggressive side absorbed first, then reclaim broken level.
- Output trappedTraderAssessment with squeezeFuelScore 0-100. Lower conviction if trappedSide=NONE.

== POC TARGET MAGNET (mean-reversion / failed-breakout / balance) ==
- POC is primary structural target; prefer over arbitrary RR if reachable and clean.
- Reject if POC too close to justify risk, or price already at/near POC for new mean-reversion entry.

== CASINO / TIME DEGRADATION ==
- NY_OPEN + CLEAN_EXPANSION_WINDOW: allow A/B if rules align.
- NY_MIDDAY / COMPRESSION_WINDOW: downgrade marginal setups (sessionConvictionAdjustment=DOWNGRADE).
- Late session: reject marginal; require liquidity event + clean invalidation.
- profitLockActive=true: stop trading / review only.

== STRUCTURAL STOP GEOMETRY (reject ATR-only generic stops) ==
- SWEEP_AND_RECLAIM: SL behind swept wick / failed auction extreme / absorption wall.
- ABSORPTION_REVERSAL: SL behind absorption wall / aggressive failed extreme.
- LVN_REJECTION_CONTINUATION: SL behind LVN/retest failure.
- FAILED_BREAKOUT_REVERSAL: SL behind failed breakout extreme.
- Populate invalidationAssessment.stopPlacementValid=false if stopProblem is not NONE.

== TRADE MANAGEMENT (ENTRY_NOW only) ==
- Scalps should move green quickly; scratch/BE if stall after trigger.
- managementPlan.moveToBETrigger: TP1_HIT | INITIAL_MOMENTUM_PULSE | STRUCTURE_RECLAIM_HOLD | NONE.
- Partial scale at first target; runner to liquidity for structural outliers.
- Marginal setup: WATCH_ONLY or WAIT, not ENTRY_NOW.
"""
