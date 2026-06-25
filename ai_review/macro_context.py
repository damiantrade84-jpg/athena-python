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


def build_macro_prompt_context(symbol: str | None, asset_type: str | None = None) -> dict[str, Any]:
    try:
        from macro.macro_guard import macro_context_for_ai

        return macro_context_for_ai(symbol, asset_type=asset_type)
    except Exception as exc:
        log.debug("macro context unavailable: %s", exc)
        return {"available": False, "reason": "unavailable", "macroEventActive": False}


def render_macro_prompt_block(symbol: str | None, asset_type: str | None = None) -> str:
    ctx = build_macro_prompt_context(symbol, asset_type)
    header = "== MACRO / FOMC RISK (server-trusted, advisory only - no execution authority) =="
    if not ctx.get("available") or not ctx.get("macroEventActive"):
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
        "If macroBlockNewTrades is true you may review the chart but MUST NOT output an "
        "execution-ready recommendation (set entryAllowedNow=false / human_action=wait).\n"
    )
