"""Tool-using advisory chat orchestrator for ATHENA AI Agent."""

from __future__ import annotations

import re
import uuid
from typing import Any, Callable

from ai_agent_logger import log_ai_chat_turn
from ai_agent_safety import validate_ai_chat_response
from ai_conversation_store import append_turn, create_or_get_thread, get_recent_turns
from ai_orchestrator import run_ai_trade_review, summarize_trade_for_chat
import ai_tools as _ai_tools


MARCUS_CHAT_SYSTEM_PROMPT = """You are Marcus Reid, a trading desk analyst.
Athena's read is a hypothesis, not truth.
Use raw market evidence, engine context, Vision, market intelligence, similar outcomes, and risk state.
If data is missing, say what is missing.
Do not invent facts. Do not claim certainty.
This is internal signal analysis, not financial advice.
Never say a trade can execute unless deterministic gates allow it.
Never override guardian, freshness, kill switch, RR, spread, fee, or risk gates.

Required answer format:
- Decision
- Market read
- Trade thesis
- Supports
- Contradictions
- Confirmation needed
- Invalidation
- Historical analogue
- Risk warning
- Final action
"""


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]


def _string_list(value: Any) -> list[str]:
    out: list[str] = []
    for item in _as_list(value):
        if isinstance(item, str):
            text = item
        else:
            text = str(item)
        if text:
            out.append(text)
    return out


def _tool_module():
    import ai_tools

    return ai_tools


def _symbol_from_context(context: dict[str, Any]) -> str | None:
    return context.get("symbol") or context.get("pair")


def _extract_comparison_symbol(message: str, primary: str | None = None) -> str | None:
    text = str(message or "").upper()
    primary_norm = str(primary or "").upper().replace("/", "")
    candidates = re.findall(r"\b[A-Z]{2,6}(?:/)?(?:USD|USDT|JPY|EUR|GBP|CHF|CAD|AUD|NZD)?\b", text)
    stop = {"COMPARE", "THIS", "SETUP", "WHICH", "CLEANER", "VISION", "ENGINE", "SCALP", "INTRADAY", "SWING"}
    for candidate in candidates:
        norm = candidate.replace("/", "")
        if norm in stop or norm == primary_norm or (primary_norm and primary_norm.startswith(norm)):
            continue
        if norm in {"BTC", "ETH", "SOL", "XRP", "BNB"}:
            return f"{norm}/USDT"
        if len(norm) >= 5:
            return candidate
    return None


def plan_tool_calls(user_message: str, context: dict) -> list[dict[str, Any]]:
    """Deterministically select read-only tools based on the user's question."""
    msg = str(user_message or "").lower()
    context = context if isinstance(context, dict) else {}
    symbol = _symbol_from_context(context)
    trace_id = context.get("trace_id")
    resolved_signal = context.get("resolved_signal") if isinstance(context.get("resolved_signal"), dict) else None
    calls: list[dict[str, Any]] = []

    def add(name: str, **args: Any) -> None:
        # Pre-resolved signal context short-circuits per-tool runtime lookup
        # so the chat reflects the operator's selected signal payload, not a
        # stale runtime cache miss.
        if resolved_signal is not None and "signal" not in args and name not in {"compare_symbols", "get_open_risk_state"}:
            args = dict(args)
            args["signal"] = resolved_signal
        if not any(call["name"] == name for call in calls):
            calls.append({"name": name, "args": args})

    # ── Research Agent routing (specific patterns first, general catch-all last) ─
    if any(token in msg for token in ("compare runs", "compare research",
                                       "run comparison")):
        add("compare_research_runs",
            baseline=context.get("baseline_run_id"),
            candidate=context.get("candidate_run_id"))
        return calls

    if any(token in msg for token in ("validate plan", "validate research")):
        add("validate_research_plan", plan=context.get("plan"))
        return calls

    if any(token in msg for token in ("propose research", "propose plan",
                                       "what should we test", "next research")):
        add("propose_research_plan", user_goal=user_message)
        return calls

    if any(token in msg for token in ("research", "backtest", "what works",
                                       "test next", "weak cluster", "strong cluster",
                                       "research plan", "research run",
                                       "recommendation", "more data", "need data",
                                       "what needs testing", "next test")):
        add("get_latest_research_summary", run_id=context.get("run_id"))
        return calls

    if any(token in msg for token in ("validate plan", "validate research")):
        add("validate_research_plan", plan=context.get("plan"))
        return calls

    if "compare" in msg or "cleaner" in msg:
        comparison = context.get("comparison_symbol") or _extract_comparison_symbol(user_message, symbol)
        add("compare_symbols", left_symbol=symbol, right_symbol=comparison, left_signal=resolved_signal)
        return calls

    add("get_signal_detail", signal_id=trace_id, symbol=symbol)

    if any(token in msg for token in ("why", "block", "blocked", "weakest", "facts")):
        add("get_engine_context", signal_id=trace_id, symbol=symbol)
        add("get_open_risk_state")
        add("get_facts_used", signal_id=trace_id, symbol=symbol)

    if any(token in msg for token in ("change your mind", "confirm", "invalidate", "h1 close", "wait")):
        add("get_engine_context", signal_id=trace_id, symbol=symbol)
        add("get_chart_vision", signal_id=trace_id, symbol=symbol)
        add("get_historical_analogues", signal_id=trace_id, symbol=symbol)

    if "similar" in msg or "history" in msg or "analogue" in msg or "analog" in msg:
        add("get_historical_analogues", signal_id=trace_id, symbol=symbol)

    if "vision" in msg or "chart" in msg or "right edge" in msg:
        add("get_chart_vision", signal_id=trace_id, symbol=symbol)

    if "strategist" in msg or "macro" in msg or "market read" in msg:
        add("get_market_intelligence", symbol=symbol, asset_type=context.get("asset_type"))
        add("get_strategist_view", signal_id=trace_id, symbol=symbol)

    if "scalp" in msg or "intraday" in msg or "swing" in msg:
        add("get_engine_context", signal_id=trace_id, symbol=symbol)
        add("get_pair_context", symbol=symbol, asset_type=context.get("asset_type"))

    if len(calls) == 1:
        add("get_engine_context", signal_id=trace_id, symbol=symbol)
        add("get_market_intelligence", symbol=symbol, asset_type=context.get("asset_type"))
        add("get_historical_analogues", signal_id=trace_id, symbol=symbol)

    return calls


def _execute_tool(call: dict[str, Any]) -> dict[str, Any]:
    name = call.get("name")
    args = dict(call.get("args") or {})
    try:
        tools = _tool_module()
        mapping: dict[str, Callable[..., dict[str, Any]]] = {
            "get_signal_detail": tools.get_signal_detail,
            "get_market_intelligence": tools.get_market_intelligence_tool,
            "get_pair_context": tools.get_pair_context_tool,
            "get_historical_analogues": tools.get_historical_analogues_tool,
            "get_chart_vision": tools.get_chart_vision_tool,
            "get_engine_context": tools.get_engine_context_tool,
            "get_open_risk_state": tools.get_open_risk_state_tool,
            "compare_symbols": tools.compare_symbols_tool,
            "get_strategist_view": tools.get_strategist_view_tool,
            "get_facts_used": tools.get_facts_used_tool,
            # Research Agent tools
            "get_latest_research_summary": tools.get_latest_research_summary_tool,
            "propose_research_plan": tools.propose_research_plan_tool,
            "validate_research_plan": tools.validate_research_plan_tool,
            "compare_research_runs": tools.compare_research_runs_tool,
            "get_research_recommendations": tools.get_research_recommendations_tool,
        }
        fn = mapping.get(str(name))
        if fn is None:
            return {"name": name, "args": args, "status": "error", "warning": "Unknown tool."}
        result = fn(**args)
        return {"name": name, "args": args, "status": _as_dict(result).get("status", "ok"), "result": result}
    except Exception as exc:
        return {"name": name, "args": args, "status": "error", "warning": str(exc), "result": None}


def _packet_from_tool_results(tool_results: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    detail = _as_dict(_as_dict(tool_results.get("get_signal_detail")).get("result"))
    packet = detail.get("packet")
    if isinstance(packet, dict):
        return packet
    engine_ctx = _as_dict(_as_dict(tool_results.get("get_engine_context")).get("result"))
    packet = engine_ctx.get("packet")
    if isinstance(packet, dict):
        return packet
    return None


def _result_status(tool_call: dict[str, Any]) -> str:
    result = _as_dict(tool_call.get("result"))
    return str(result.get("status") or tool_call.get("status") or "unknown")


def _facts_from_tools(tool_results: dict[str, dict[str, Any]], summary: dict[str, Any]) -> list[str]:
    facts = list(summary.get("facts_used") or [])
    for name, call in tool_results.items():
        facts.append(f"{name}: status={_result_status(call)}")
    detail = _as_dict(_as_dict(tool_results.get("get_signal_detail")).get("result"))
    signal = _as_dict(detail.get("signal"))
    if signal:
        for key in ("gate_result", "gateResult", "candidate_status", "finalState", "blockReason"):
            if signal.get(key) is not None:
                facts.append(f"{key}={signal.get(key)}")
    return facts


def _similar_summary(tool_results: dict[str, dict[str, Any]], decision: dict[str, Any]) -> str | None:
    similar = _as_dict(_as_dict(tool_results.get("get_historical_analogues")).get("result"))
    if not similar:
        packet = _packet_from_tool_results(tool_results) or {}
        similar = _as_dict(packet.get("similar_outcomes"))
    if "analogues" in similar and isinstance(similar.get("analogues"), dict):
        similar = _as_dict(similar.get("analogues"))
    n = int(similar.get("aggregate_sample_size") or 0)
    reliability = similar.get("reliability") or "unavailable"
    if n < 20:
        return f"Similar setup sample is {n}; reliability is {reliability}, so no calibrated probability claim is allowed."
    return decision.get("historical_evidence_summary")


def compose_trader_answer(user_message: str, tool_results: dict, packet: dict | None) -> dict[str, Any]:
    """Compose a deterministic trading-desk response from actual tool results."""
    packet = packet if isinstance(packet, dict) else None
    decision = run_ai_trade_review(packet or {}, mode="chat") if packet else {
        "decision": "DATA_INSUFFICIENT",
        "market_read": "No linked signal packet is available.",
        "thesis": "Select a signal or provide a trace_id for signal-specific analysis.",
        "supports": [],
        "contradictions": [],
        "confirmation_needed": ["Select a signal or provide trace_id/symbol context."],
        "invalidation": None,
        "risk_notes": ["Risk, RR, freshness, guardian, and kill-switch state are unavailable."],
        "historical_evidence_summary": "Historical scaffold unavailable.",
        "final_action": "advisory_review_only",
        "contradiction_flags": [],
    }
    summary = summarize_trade_for_chat(packet or {})
    facts_used = _facts_from_tools(tool_results, summary)
    missing_data = list(summary.get("missing_data") or [])

    for call in tool_results.values():
        result = _as_dict(call.get("result"))
        missing_data.extend(_string_list(result.get("missing_fields")))
        warning = result.get("warning")
        if warning:
            missing_data.append(str(warning))
        missing_data.extend(_string_list(result.get("warnings")))

    market_intelligence = summary.get("market_intelligence") or {"freshness_status": "unavailable", "warnings": []}
    if tool_results.get("get_market_intelligence"):
        mi_result = _as_dict(_as_dict(tool_results["get_market_intelligence"]).get("result"))
        if mi_result:
            candidate_mi = mi_result.get("market_intelligence") or mi_result
            current_status = _as_dict(market_intelligence).get("freshness_status")
            candidate_status = _as_dict(candidate_mi).get("freshness_status")
            if (not market_intelligence or current_status in {None, "unavailable"}) and candidate_status != "unavailable":
                market_intelligence = candidate_mi
    if isinstance(market_intelligence, dict):
        macro = _as_dict(market_intelligence.get("macro_regime"))
        if "risk_regime" not in market_intelligence and macro.get("risk_regime") is not None:
            market_intelligence = dict(market_intelligence)
            market_intelligence["risk_regime"] = macro.get("risk_regime")
        if "calendar_within_72h" not in market_intelligence and macro.get("calendar_within_72h") is not None:
            market_intelligence = dict(market_intelligence)
            market_intelligence["calendar_within_72h"] = macro.get("calendar_within_72h")

    vision_summary = summary.get("vision_summary") or {"freshness_status": "unknown", "allowed_for_execution_context": False}
    if tool_results.get("get_chart_vision"):
        vision_result = _as_dict(_as_dict(tool_results["get_chart_vision"]).get("result"))
        vision = _as_dict(vision_result.get("vision"))
        if vision:
            vision_summary = {
                "right_edge_status": vision.get("right_edge_status"),
                "tf_alignment": vision.get("tf_alignment"),
                "freshness_status": vision.get("freshness_status"),
                "allowed_for_execution_context": bool(vision.get("allowed_for_execution_context", False)),
                "style_ratings": vision.get("style_ratings") or {},
                "visible_obstacles": _as_dict(vision.get("visible_structure")).get("obstacles_before_tp") or [],
                "memo": vision.get("memo"),
            }

    strategist_summary = None
    if tool_results.get("get_strategist_view"):
        strategist_result = _as_dict(_as_dict(tool_results["get_strategist_view"]).get("result"))
        strategist_summary = strategist_result.get("strategist") or strategist_result.get("view")

    supports = _string_list(decision.get("supports"))
    contradictions = _string_list(decision.get("contradictions"))
    confirmation_needed = _string_list(decision.get("confirmation_needed"))
    risk_notes = _string_list(decision.get("risk_notes"))
    historical = _similar_summary(tool_results, decision)

    if _as_dict(market_intelligence).get("freshness_status") in {None, "unavailable", "stale"}:
        missing_data.append("Market intelligence unavailable or stale; no macro confirmation inferred.")
    if not _as_dict(vision_summary).get("allowed_for_execution_context"):
        missing_data.append("Vision missing, stale, timestamp-incomplete, or UI-only for execution context.")

    sections = [
        f"Decision: {decision.get('decision')}",
        f"Market read: {decision.get('market_read') or 'No complete market read available.'}",
        f"Trade thesis: {decision.get('thesis') or 'Advisory review only.'}",
        "Supports: " + ("; ".join(supports) if supports else "No complete support set available."),
        "Contradictions: " + ("; ".join(contradictions) if contradictions else "No deterministic contradictions reported by available tools."),
        "Confirmation needed: " + ("; ".join(confirmation_needed) if confirmation_needed else "Fresh deterministic gates and risk checks must remain clean."),
        f"Invalidation: {decision.get('invalidation') or 'Use deterministic signal invalidation; missing from linked data.'}",
        f"Historical analogue: {historical or 'Historical analogue scaffold unavailable.'}",
        f"Vision read: right edge={_as_dict(vision_summary).get('right_edge_status') or 'unknown'}, freshness={_as_dict(vision_summary).get('freshness_status') or 'unknown'}, execution_context={bool(_as_dict(vision_summary).get('allowed_for_execution_context'))}",
        "Risk warning: " + ("; ".join(risk_notes) if risk_notes else "No extra risk note beyond deterministic gates."),
        f"Final action: {decision.get('final_action') or 'advisory_review_only'}",
    ]
    if missing_data:
        sections.append("Missing data: " + "; ".join(sorted(set(missing_data))[:12]))

    return {
        "decision": decision.get("decision"),
        "answer": "\n".join(sections),
        "market_read": decision.get("market_read"),
        "trade_thesis": decision.get("thesis"),
        "supports": supports,
        "contradictions": contradictions,
        "confirmation_needed": confirmation_needed,
        "invalidation": decision.get("invalidation"),
        "historical_analogue_summary": historical,
        "risk_warning": "; ".join(risk_notes) if risk_notes else None,
        "vision_summary": vision_summary,
        "strategist_summary": strategist_summary,
        "market_intelligence": market_intelligence,
        "facts_used": facts_used,
        "missing_data": sorted(set(str(item) for item in missing_data if item)),
        "contradiction_flags": _string_list(decision.get("contradiction_flags")),
        "final_action": decision.get("final_action"),
    }


def _resolve_chat_context(request: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Resolve the signal context using a deterministic priority order.

    Priority: 1) runtime trace_id lookup, 2) explicit request signal payload,
    3) runtime symbol lookup, 4) symbol-only, 5) none. Returns the resolved
    signal (or None) plus a context_resolution diagnostic block.
    """
    trace_id = str(request.get("trace_id") or "").strip() or None
    symbol = str(request.get("symbol") or "").strip() or None
    signal_payload = request.get("signal") if isinstance(request.get("signal"), dict) else None

    resolution: dict[str, Any] = {
        "mode": "none",
        "trace_id_received": bool(trace_id),
        "signal_payload_received": bool(signal_payload),
        "resolved_symbol": None,
        "resolved_engine": None,
        "warnings": [],
    }
    resolved: dict[str, Any] | None = None

    # 1. Runtime trace_id lookup
    if trace_id:
        try:
            found, _lookup = _ai_tools._find_signal(signal_id=trace_id, symbol=None)
            if isinstance(found, dict) and found:
                resolved = found
                resolution["mode"] = "trace_id"
        except Exception as exc:
            resolution["warnings"].append(f"trace_id lookup failed: {exc}")

    # 2. Explicit signal payload from request
    if resolved is None and signal_payload:
        resolved = dict(signal_payload)
        resolution["mode"] = "request_signal_payload"

    # 3. Runtime symbol lookup (latest selected/live signal for this symbol)
    if resolved is None and symbol:
        try:
            found, _lookup = _ai_tools._find_signal(signal_id=None, symbol=symbol)
            if isinstance(found, dict) and found:
                resolved = found
                resolution["mode"] = "latest_symbol_signal"
        except Exception as exc:
            resolution["warnings"].append(f"symbol lookup failed: {exc}")

    # 4/5. Symbol-only fallback or no context
    if resolved is None:
        resolution["mode"] = "symbol_only" if symbol else "none"

    if resolved is not None:
        resolution["resolved_symbol"] = (
            resolved.get("symbol")
            or resolved.get("pair")
            or resolved.get("display")
            or symbol
        )
        resolution["resolved_engine"] = (
            resolved.get("engine")
            or resolved.get("engine_source")
            or resolved.get("source_engine")
        )
    else:
        resolution["resolved_symbol"] = symbol

    return resolved, resolution


def run_trade_chat_turn(request: dict) -> dict[str, Any]:
    request = request if isinstance(request, dict) else {}
    message = str(request.get("message") or "").strip()
    if not message:
        return {"error": "message_required"}

    audit_db = request.get("_audit_db") or request.get("audit_db")
    session_id = create_or_get_thread(
        request.get("session_id"),
        request.get("symbol"),
        request.get("trace_id"),
        db_path=audit_db,
        metadata={"source": "ai_trade_chat.v1"},
    )
    if not session_id:
        session_id = f"ai-chat-{uuid.uuid4().hex}"

    resolved_signal, context_resolution = _resolve_chat_context(request)

    context = {
        "trace_id": request.get("trace_id"),
        "symbol": request.get("symbol"),
        "asset_type": request.get("asset_type"),
        "style": request.get("style"),
        "comparison_symbol": request.get("comparison_symbol"),
        "resolved_signal": resolved_signal,
    }
    tool_plan = plan_tool_calls(message, context)
    if request.get("include_vision") is False:
        tool_plan = [call for call in tool_plan if call.get("name") != "get_chart_vision"]
    if request.get("include_similar_setups") is False:
        tool_plan = [call for call in tool_plan if call.get("name") != "get_historical_analogues"]

    tool_calls = [_execute_tool(call) for call in tool_plan]
    tool_results = {str(call.get("name")): call for call in tool_calls}
    packet = _packet_from_tool_results(tool_results)
    response = compose_trader_answer(message, tool_results, packet)
    response.update(
        {
            "session_id": session_id,
            "trace_id": (packet or {}).get("trace_id") or request.get("trace_id"),
            "symbol": (packet or {}).get("symbol") or request.get("symbol"),
            "context_resolution": context_resolution,
            "tool_calls": [
                _compact_tool_call(call)
                for call in tool_calls
            ],
            "safety": {
                "read_only": True,
                "can_execute": False,
                "can_modify_thresholds": False,
                "deterministic_gates_required": True,
            },
        }
    )
    response = validate_ai_chat_response(response, packet)

    append_turn(session_id, "user", message, db_path=audit_db)
    append_turn(
        session_id,
        "assistant",
        response.get("answer") or "",
        tool_calls=response.get("tool_calls") or [],
        facts_used=response.get("facts_used") or [],
        decision=response.get("decision"),
        db_path=audit_db,
    )
    response["recent_turns"] = get_recent_turns(session_id, limit=12, db_path=audit_db)
    log_ai_chat_turn(
        thread_id=session_id,
        trace_id=response.get("trace_id"),
        symbol=response.get("symbol"),
        user_message=message,
        assistant_answer=response.get("answer") or "",
        tools_called=response.get("tool_calls") or [],
        facts_used=response.get("facts_used") or [],
        decision=response.get("decision"),
        safety_flags=response.get("safety_flags") or [],
        missing_data=response.get("missing_data") or [],
        contradiction_flags=response.get("contradiction_flags") or [],
        model_used="deterministic_fallback",
        prompt=MARCUS_CHAT_SYSTEM_PROMPT,
    )
    return response


def _compact_tool_call(call: dict[str, Any]) -> dict[str, Any]:
    result = _as_dict(call.get("result"))
    return {
        "name": call.get("name"),
        "args": call.get("args") or {},
        "status": call.get("status"),
        "warnings": _string_list(result.get("warnings")) or ([str(call.get("warning"))] if call.get("warning") else []),
        "freshness_status": result.get("freshness_status")
        or _as_dict(result.get("market_intelligence")).get("freshness_status")
        or _as_dict(result.get("pair_context")).get("freshness_status")
        or _as_dict(result.get("vision")).get("freshness_status"),
    }
