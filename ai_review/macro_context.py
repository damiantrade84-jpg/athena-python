"""Macro (FOMC) risk context for chart AI review — advisory only, no execution authority.

Server-trusted: computed on the backend from the local macro store via the macro guard.
The AI may review during a macro lockout but must not output an execution-ready
recommendation (the hard lockout is enforced deterministically elsewhere).
"""

from __future__ import annotations

import json
import logging
from typing import Any

log = logging.getLogger("ai_review.macro")


_TONE_INSTRUCTION = (
    "FOMC tone is macro context only. Do not recommend execution purely because the tone "
    "is hawkish or dovish. During macro lockout, do not recommend immediate execution. Use "
    "the tone to evaluate whether post-news chart structure has macro support or conflict."
)


def _fomc_tone_block(symbol: str | None, asset_type: str | None) -> dict[str, Any] | None:
    """Latest FOMC tone as advisory CONTEXT for one symbol. Fail-safe → None."""
    try:
        import json

        from macro.fomc_tone import expected_pressure
        from macro.tone_store import default_tone_store

        rec = default_tone_store().latest()
        if not rec:
            return None
        score = rec.get("tone_score")
        confidence = rec.get("confidence")
        low_conf = not isinstance(confidence, (int, float)) or confidence < 0.40
        warnings = rec.get("warnings_json")
        if isinstance(warnings, str):
            try:
                warnings = json.loads(warnings)
            except (ValueError, TypeError):
                warnings = []
        evidence = rec.get("evidence_json")
        if isinstance(evidence, str):
            try:
                evidence = json.loads(evidence)
            except (ValueError, TypeError):
                evidence = []
        return {
            "label": "UNKNOWN" if low_conf else rec.get("tone_label"),
            "score": score,
            "confidence": confidence,
            "executionUse": "CONTEXT_ONLY",
            "currency": rec.get("currency"),
            "componentScores": {
                "statementLanguage": rec.get("statement_language_score"),
                "inflationLanguage": rec.get("inflation_language_score"),
                "laborMarket": rec.get("labor_market_score"),
                "growth": rec.get("growth_score"),
                "balanceSheet": rec.get("balance_sheet_score"),
                "forwardGuidance": rec.get("forward_guidance_score"),
                "rateDecision": rec.get("rate_decision_score"),
            },
            "majorEvidence": (evidence or [])[:5],
            "warnings": warnings or [],
            "expectedPressure": expected_pressure(symbol, int(score) if isinstance(score, (int, float)) else 0, asset_type),
            "warning": "Do not execute from tone alone. Wait for post-news structure.",
        }
    except Exception as exc:
        log.debug("fomc tone context unavailable: %s", exc)
        return None


def build_macro_prompt_context(symbol: str | None, asset_type: str | None = None) -> dict[str, Any]:
    try:
        from macro.macro_guard import macro_context_for_ai

        ctx = macro_context_for_ai(symbol, asset_type=asset_type)
    except Exception as exc:
        log.debug("macro context unavailable: %s", exc)
        ctx = {"available": False, "reason": "unavailable", "macroEventActive": False}
    tone = _fomc_tone_block(symbol, asset_type)
    if tone is not None:
        ctx["fomcTone"] = tone
    return ctx


def _tone_lines(ctx: dict[str, Any]) -> str:
    if not ctx.get("fomcTone"):
        return ""
    return f"FOMC TONE INSTRUCTION: {_TONE_INSTRUCTION}\n"


def render_macro_prompt_block(symbol: str | None, asset_type: str | None = None) -> str:
    ctx = build_macro_prompt_context(symbol, asset_type)
    header = "== MACRO / FOMC RISK (server-trusted, advisory only - no execution authority) =="
    # Render the full block when a macro event is active OR a recent tone exists.
    if (not ctx.get("available") or not ctx.get("macroEventActive")) and not ctx.get("fomcTone"):
        summary = {
            "available": bool(ctx.get("available", False)),
            "macroEventActive": False,
            "macroRisk": ctx.get("macroRisk") or "NONE",
        }
        return f"{header}\n{json.dumps({'macroContext': summary}, default=str)}\n"
    block = json.dumps({"macroContext": ctx}, default=str, indent=2)
    return (
        f"{header}\n{block}\n"
        f"MACRO INSTRUCTION: {ctx.get('instruction') or ''}\n"
        f"{_tone_lines(ctx)}"
        "If macroBlockNewTrades is true you may review the chart but MUST NOT output an "
        "execution-ready recommendation (set entryAllowedNow=false / human_action=wait).\n"
    )
