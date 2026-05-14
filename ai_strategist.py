"""Read-only strategist layer for ATHENA AI Agent Phase 2."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ai_contradiction_detector import detect_ai_contradictions
from market_intelligence import get_market_intelligence


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def strategist_morning_brief(asset_scope: str = "all") -> dict[str, Any]:
    mi = get_market_intelligence(None, None)
    warnings = list(mi.get("warnings") or [])
    macro = mi.get("macro_regime") or {}
    calendar = list(macro.get("calendar_within_72h") or [])
    headline = "Market intelligence is unavailable." if mi.get("freshness_status") == "unavailable" else "Market desk context is partially available."
    full = (
        f"{headline} Risk regime: {macro.get('risk_regime', 'unknown')}. "
        "Strategist output is advisory-only and does not alter Athena gates."
    )
    return {
        "schema_version": "strategist_brief.v1",
        "generated_at": _now_iso(),
        "asset_scope": asset_scope,
        "headline": headline,
        "macro_regime": macro.get("risk_regime", "unknown"),
        "key_risks": warnings[:8],
        "watchlist": [],
        "avoid_conditions": ["Avoid treating stale or unavailable macro context as confirmation."],
        "open_positions_summary": "Not inspected by Phase 2 strategist scaffold.",
        "yesterday_outcomes": "Not enough local outcome context for a daily outcome summary.",
        "calendar_risks": calendar,
        "data_warnings": warnings,
        "full_brief": full,
    }


def strategist_pre_trade_check(packet: dict) -> dict[str, Any]:
    packet = packet if isinstance(packet, dict) else {}
    contradictions = detect_ai_contradictions(packet)
    warnings = list(packet.get("market_intelligence", {}).get("warnings") or [])
    if contradictions.get("severity") in {"critical", "high"}:
        verdict = "OBJECT"
        action = "require_user_review"
        reason = "High-severity deterministic contradiction detected."
    elif packet.get("market_intelligence", {}).get("freshness_status") in {"unavailable", "stale"}:
        verdict = "DATA_INSUFFICIENT"
        action = "data_refresh_required"
        reason = "Market intelligence is unavailable or stale."
    elif contradictions.get("flags"):
        verdict = "WATCH_ONLY"
        action = "downgrade_to_watchlist"
        reason = "Non-critical contradictions require confirmation."
    else:
        verdict = "CONCUR"
        action = "no_change"
        reason = "No strategist objection from available advisory context."
    return {
        "schema_version": "strategist_pre_trade.v1",
        "verdict": verdict,
        "reason": reason,
        "macro_conflict": packet.get("market_intelligence", {}).get("freshness_status") in {"unavailable", "stale"},
        "portfolio_conflict": False,
        "event_risk_conflict": bool((packet.get("market_intelligence", {}).get("macro_regime") or {}).get("calendar_within_72h")),
        "recommended_action": action,
        "warnings": warnings + list(contradictions.get("flags") or []),
        "advisory_only": True,
        "execution_allowed": False,
    }


def weekly_strategy_retrospective(lookback_days: int = 7) -> dict[str, Any]:
    return {
        "schema_version": "strategist_weekly.v1",
        "generated_at": _now_iso(),
        "top_winning_patterns": [],
        "top_losing_patterns": [],
        "regime_misreads": [],
        "factor_adjustment_candidates": [],
        "research_recommendations": [
            "Use this retrospective as a research queue only; do not auto-apply threshold changes."
        ],
        "do_not_auto_apply": True,
        "full_memo": f"No calibrated weekly retrospective available for the last {int(lookback_days)} day(s).",
    }
