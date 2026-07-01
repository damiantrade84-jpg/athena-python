"""ai_agent_planner.py — native LLM function-calling planner for the AI agent.

Phase 2 of the Athena AI Edge Program. Config-gated by
`AI_AGENT_NATIVE_PLANNING_ENABLED` (default False). When disabled, the
existing deterministic keyword-based `plan_tool_calls()` in ai_trade_chat.py
is used (zero behavior change).

When enabled, the planner:
1. Calls the LLM with read-only tool definitions (OpenAI function-calling
   schema).
2. The LLM proposes a list of tool calls.
3. Each proposed call is validated against the read-only registry; any tool
   not in `READ_ONLY_TOOL_NAMES` is rejected. This is the safety gate.
4. Validated calls are returned to the executor (which runs them and feeds
   results back to the LLM for the final answer).

Safety:
- ALL tools in the registry are read-only. The planner never exposes
  mutating tools to the LLM.
- Every proposed call is re-validated against the registry before execution.
- Bounded by `AI_AGENT_MAX_STEPS` (default 8) to prevent runaway loops.
- Never raises; on any LLM/parse error, returns an empty call list so the
  caller falls back to the keyword planner.
- Advisory-only. No execution, no order approval, no gate override.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from config import (
    AITemperatureConfig,
    CONFIG,
    create_ai_client,
    get_ai_api_key,
    get_ai_max_retries,
    get_ai_model,
    get_ai_timeout_sec,
    resolve_ai_review_runtime,
)

log = logging.getLogger("athena.ai_agent_planner")

# Read-only tool registry. Any tool proposed by the LLM that is NOT in this
# set is rejected. This is the safety gate that prevents the LLM from
# inventing mutating tools.
READ_ONLY_TOOL_NAMES = frozenset(
    {
        "get_signal_detail",
        "get_engine_context",
        "get_chart_vision",
        "get_historical_analogues",
        "get_market_intelligence",
        "get_strategist_view",
        "get_pair_context",
        "get_open_risk_state",
        "get_facts_used",
        "compare_symbols",
    }
)


def is_native_planning_enabled() -> bool:
    try:
        return bool(CONFIG.get("AI_AGENT_NATIVE_PLANNING_ENABLED", False))
    except Exception:
        return False


def get_max_steps() -> int:
    try:
        n = int(CONFIG.get("AI_AGENT_MAX_STEPS", 8))
        return max(1, min(n, 32))
    except Exception:
        return 8


def build_tool_definitions() -> list[dict[str, Any]]:
    """OpenAI function-calling tool definitions for the read-only registry.

    Each definition is a JSON schema describing the tool's name, purpose, and
    arguments. The LLM uses these to propose tool calls.
    """
    return [
        {
            "type": "function",
            "function": {
                "name": "get_signal_detail",
                "description": "Get the full detail for the currently-selected signal (advisory, read-only).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "signal_id": {"type": "string", "description": "trace_id of the signal"},
                        "symbol": {"type": "string", "description": "pair/symbol fallback if signal_id absent"},
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_engine_context",
                "description": "Get Engine A/B/C/D context for the signal (read-only).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "signal_id": {"type": "string"},
                        "symbol": {"type": "string"},
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_chart_vision",
                "description": "Get the AI Vision chart read for the signal (advisory, read-only).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "signal_id": {"type": "string"},
                        "symbol": {"type": "string"},
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_historical_analogues",
                "description": "Retrieve similar historical setup analogues (read-only).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "signal_id": {"type": "string"},
                        "symbol": {"type": "string"},
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_market_intelligence",
                "description": "Get market intelligence / macro context for a symbol (read-only).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "symbol": {"type": "string"},
                        "asset_type": {"type": "string"},
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_strategist_view",
                "description": "Get the strategist's view on the signal (advisory, read-only).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "signal_id": {"type": "string"},
                        "symbol": {"type": "string"},
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_pair_context",
                "description": "Get per-pair context (read-only).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "symbol": {"type": "string"},
                        "asset_type": {"type": "string"},
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_open_risk_state",
                "description": "Get the current open-risk / portfolio heat state (read-only).",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_facts_used",
                "description": "Get the facts/data points used by the engine for this signal (read-only).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "signal_id": {"type": "string"},
                        "symbol": {"type": "string"},
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "compare_symbols",
                "description": "Compare two symbols/signals side-by-side (read-only).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "left_symbol": {"type": "string"},
                        "right_symbol": {"type": "string"},
                    },
                },
            },
        },
    ]


def validate_tool_calls(proposed: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Filter proposed tool calls to only read-only registry tools.

    Rejects any tool not in READ_ONLY_TOOL_NAMES. Rejects calls with non-dict
    args. Never raises.
    """
    out: list[dict[str, Any]] = []
    if not isinstance(proposed, list):
        return out
    seen: set[str] = set()
    for call in proposed:
        if not isinstance(call, dict):
            continue
        name = str(call.get("name") or "").strip()
        if not name or name not in READ_ONLY_TOOL_NAMES:
            log.warning("[AI_AGENT_PLANNER] rejected non-registry tool: %r", name)
            continue
        args = call.get("args") or call.get("arguments") or {}
        if not isinstance(args, dict):
            try:
                args = json.loads(args) if isinstance(args, str) else {}
            except Exception:
                args = {}
        # Dedupe by name (matches keyword planner behavior)
        if name in seen:
            continue
        seen.add(name)
        out.append({"name": name, "args": args})
    return out


def plan_tool_calls_with_llm(
    user_message: str,
    context: dict[str, Any],
    *,
    available_tools: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Call the LLM with tool definitions; returns validated tool calls.

    Fail-closed: on any error (no api key, LLM failure, parse error), returns
    an empty list so the caller falls back to the keyword planner. Never
    raises.
    """
    if not is_native_planning_enabled():
        return []
    if not user_message or not user_message.strip():
        return []
    try:
        runtime = resolve_ai_review_runtime(CONFIG)
        active_provider = str(
            runtime.get("provider") or runtime.get("selectedProvider") or "grok"
        )
        api_key = (get_ai_api_key(CONFIG) or str(runtime.get("api_key") or "") or "").strip()
        if not api_key:
            return []
        model = str(get_ai_model(CONFIG, preferred_key="MARCUS_MODEL", provider=active_provider)).strip()
        if not model:
            return []
        client = create_ai_client(
            CONFIG,
            api_key=api_key,
            max_retries=get_ai_max_retries(CONFIG, preferred_key="AI_SDK_MAX_RETRIES"),
            provider=active_provider,
        )
        tools = available_tools if available_tools is not None else build_tool_definitions()
        symbol = (context or {}).get("symbol") or (context or {}).get("pair") or ""
        trace_id = (context or {}).get("trace_id") or ""
        planner_prompt = (
            "You are a read-only tool planner for the Athena AI trading agent. "
            "Select 0-4 read-only tools that would help answer the user's question. "
            "Only use tools from the provided list. Never invent tool names. "
            "Return a JSON array of objects with 'name' and 'args' keys. "
            "If no tools are needed, return an empty array."
        )
        user_content = (
            f"User question: {user_message}\n"
            f"Symbol: {symbol}\n"
            f"Trace ID: {trace_id}\n"
            "Propose tool calls as a JSON array."
        )
        completion = client.chat.completions.create(
            model=model,
            max_tokens=400,
            temperature=float(AITemperatureConfig.get_temperature("marcus")),
            timeout=get_ai_timeout_sec(CONFIG),
            messages=[
                {"role": "system", "content": planner_prompt},
                {"role": "user", "content": user_content},
            ],
            tools=tools,
            tool_choice="auto",
        )
        # Extract tool calls from the completion
        proposed: list[dict[str, Any]] = []
        try:
            msg = completion.choices[0].message
            tool_calls = getattr(msg, "tool_calls", None) or []
            for tc in tool_calls:
                try:
                    fn = getattr(tc, "function", None)
                    if fn is None:
                        continue
                    name = getattr(fn, "name", "") or ""
                    args_str = getattr(fn, "arguments", "") or "{}"
                    try:
                        args = json.loads(args_str) if isinstance(args_str, str) else dict(args_str)
                    except Exception:
                        args = {}
                    proposed.append({"name": str(name), "args": args})
                except Exception:
                    continue
        except Exception:
            # Some providers return text instead of structured tool calls
            text = ""
            try:
                text = (completion.choices[0].message.content or "").strip()
            except Exception:
                pass
            if text:
                try:
                    parsed = json.loads(text)
                    if isinstance(parsed, list):
                        for item in parsed:
                            if isinstance(item, dict):
                                proposed.append(item)
                except Exception:
                    pass
        return validate_tool_calls(proposed)
    except Exception as exc:
        log.warning("[AI_AGENT_PLANNER] LLM planning failed; returning empty list: %s", exc)
        return []
