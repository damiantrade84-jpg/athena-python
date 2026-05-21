"""Prompt builder for AI chart review."""

from __future__ import annotations

import json
from typing import Any


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

    return f"""You are reviewing a trading setup using:
1. a native chart PNG screenshot (provided as an image input)
2. server-trusted Engine A diagnostics
3. ATR / SL / TP / RR / freshness diagnostics

Return strict JSON only, matching this schema exactly:
{{ verdict, confidence, setup_type, visual_confirmation, visual_contradiction,
  engine_a_alignment, atr_rr_assessment, freshness_assessment, entry_quality,
  supporting_reasons, risks, missing_context, human_action }}

Rules:
- Do not approve a trade only because Engine A score is high.
- Check whether the screenshot visually agrees with Engine A direction.
- ATR freshness uses timeframe-aware logic. D1 confirmed-only ATR can naturally
  be 24–48h old — do not flag it as stale solely on age.
- Check SL/TP/RR quality and whether price has drifted from candidate entry.
- Treat any field labelled "unavailable" as uncertainty, not as zero.
- Treat provider/timestamp mismatch as uncertainty.
- This is review-only. Do not issue execution instructions.

== SYMBOL ==
{context.get("symbol")} {context.get("timeframe")} asset_group: {context.get("asset_group")}

== ENGINE A (server-trusted) ==
direction:           {_fmt(context.get("direction"))}
confluence_score:    {_fmt(context.get("confluence_score"))}
threshold:           {_fmt(context.get("threshold"))}
max_score_override:  {_fmt(context.get("max_score_override"))}
passed:              {_fmt(context.get("passed"))}
regime:              {_fmt(context.get("regime"))}
equity_session:      applied={_fmt(equity.get("applied"))} utc_hour={_fmt(equity.get("utc_hour"))} multiplier={_fmt(equity.get("multiplier"))} reason={_fmt(equity.get("reason"))}
factor_diagnostics:  {_fmt(context.get("factor_diagnostics"))}
multiplier_diagnostics: {_fmt(context.get("multiplier_diagnostics"))}
directional_alignment: {_fmt(context.get("directional_alignment"))}

== ATR ==
atr_value:           {_fmt(atr.get("atr_value"))}
atr_tf:              {_fmt(atr.get("atr_tf"))}
atr_source:          {_fmt(atr.get("atr_source"))}
atr_confirmed_only:  {_fmt(atr.get("atr_confirmed_only"))}
atr_age_seconds:     {_fmt(atr.get("atr_age_seconds"))}
atr_freshness:       {_fmt(atr.get("atr_freshness_status"))} (max_expected={_fmt(atr.get("max_expected_age_seconds"))}s)
atr_cache_hit:       {_fmt(atr.get("atr_cache_hit"))}

== GEOMETRY ==
candidate_entry:     {_fmt(geometry.get("candidate_entry"))}
current_price:       {_fmt(geometry.get("current_price"))}
stop_loss:           {_fmt(geometry.get("stop_loss"))}
take_profit:         {_fmt(geometry.get("take_profit"))}
risk_points:         {_fmt(geometry.get("risk_points"))}
reward_points:       {_fmt(geometry.get("reward_points"))}
rr:                  {_fmt(geometry.get("rr"))}
price_displacement_from_candidate_entry: {_fmt(geometry.get("price_displacement_from_candidate_entry"))}
sl_tp_source:        {_fmt(geometry.get("sl_tp_source"))}

== TIMESTAMPS ==
scan_timestamp:        {_fmt(context.get("scan_timestamp"))}
candidate_timestamp:   {_fmt(context.get("candidate_timestamp"))}
latest_candle_ts:      {_fmt(context.get("latest_candle_ts"))}
d1_ts / h4_ts / h1_ts: {_fmt(context.get("d1_candle_ts"))} / {_fmt(context.get("h4_candle_ts"))} / {_fmt(context.get("h1_candle_ts"))}
chart_captured_at:     {_fmt(context.get("chart_captured_at"))}
provider (engine A):   {_fmt(context.get("engine_a_provider"))}
provider (chart):      {_fmt(context.get("chart_provider_hint"))}    provider_mismatch={_fmt(context.get("provider_mismatch"))}
mismatch_warnings:     {_fmt(mismatch_warnings)}

Now analyse the chart and return JSON only.
"""
