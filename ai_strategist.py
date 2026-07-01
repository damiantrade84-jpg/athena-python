"""Read-only strategist layer for ATHENA AI Agent Phase 2."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from ai_contradiction_detector import detect_ai_contradictions
from market_intelligence import get_market_intelligence

log = logging.getLogger("athena.ai_strategist")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Phase 4 — Optional LLM narrative augmentation (advisory-only, default-off)
# ---------------------------------------------------------------------------


def _strategist_llm_enabled() -> bool:
    try:
        from config import CONFIG

        return bool(CONFIG.get("AI_STRATEGIST_LLM_ENABLED", False))
    except Exception:
        return False


def _strategist_llm_narrative(kind: str, context: dict[str, Any]) -> str | None:
    """Call the LLM to produce an advisory narrative for a strategist surface.

    Config-gated by AI_STRATEGIST_LLM_ENABLED (default false). Fail-closed:
    returns None on any error, missing key, or disabled state. Never raises.
    The narrative is purely additive — it never alters deterministic verdicts,
    recommended actions, or execution gates.
    """
    if not _strategist_llm_enabled():
        return None
    try:
        from config import (
            AITemperatureConfig,
            CONFIG,
            create_ai_client,
            get_ai_api_key,
            get_ai_model,
            get_ai_timeout_sec,
            resolve_ai_review_runtime,
        )

        runtime = resolve_ai_review_runtime(CONFIG)
        active_provider = str(runtime.get("provider") or runtime.get("selectedProvider") or "grok")
        api_key = (get_ai_api_key(CONFIG) or str(runtime.get("api_key") or "") or "").strip()
        if not api_key:
            return None
        model = str(get_ai_model(CONFIG, preferred_key="STRATEGIST_MODEL", provider=active_provider) or "").strip()
        if not model:
            return None
        client = create_ai_client(
            CONFIG,
            api_key=api_key,
            provider=active_provider,
        )
        system = (
            "You are an advisory macro strategist for a paper-trading desk. "
            "Produce a concise narrative (3-6 sentences) that interprets the "
            "provided deterministic context. Never claim authority to approve, "
            "block, or execute trades. Never cite thresholds or weights. "
            "Advisory-only."
        )
        user = f"Surface: {kind}\nContext: {json.dumps(context, default=str)[:6000]}\nNarrative:"
        completion = client.chat.completions.create(
            model=model,
            max_tokens=300,
            temperature=float(AITemperatureConfig.get_temperature("marcus")),
            timeout=get_ai_timeout_sec(CONFIG),
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        text = (completion.choices[0].message.content or "").strip()
        return text or None
    except Exception as exc:
        log.warning("[AI_STRATEGIST] LLM narrative failed (fail-closed -> None): %s", exc)
        return None


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
        "llm_narrative": _strategist_llm_narrative(
            "morning_brief",
            {"asset_scope": asset_scope, "macro_regime": macro.get("risk_regime", "unknown"), "key_risks": warnings[:8]},
        ),
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
        "llm_narrative": _strategist_llm_narrative(
            "pre_trade_check",
            {"verdict": verdict, "reason": reason, "contradictions": contradictions},
        ),
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
        "llm_narrative": _strategist_llm_narrative(
            "weekly_retrospective",
            {"lookback_days": lookback_days},
        ),
    }
