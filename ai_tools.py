"""Read-only AI tool registry for ATHENA advisory surfaces.

These helpers expose stable JSON-like shapes for chat/tool callers. They only
read supplied runtime/cache state and Phase 1/2 advisory modules; they never
import execution or broker adapters.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Callable

from ai_context import build_ai_review_packet
from ai_orchestrator import build_packet_for_signal, run_ai_trade_review, summarize_trade_for_chat
from ai_similar_setups import find_similar_setups
from ai_strategist import (
    strategist_morning_brief,
    strategist_pre_trade_check,
    weekly_strategy_retrospective,
)
from market_intelligence import get_market_intelligence
from pair_context import build_pair_context

_RUNTIME: Any = None


def set_ai_tools_runtime(runtime: Any) -> None:
    """Set optional read-only runtime used by signal/risk lookup helpers."""
    global _RUNTIME
    _RUNTIME = runtime


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _symbol_from(signal: dict[str, Any]) -> str | None:
    value = signal.get("symbol") or signal.get("pair") or signal.get("display") or signal.get("ticker")
    text = str(value or "").strip()
    return text or None


@contextmanager
def _maybe_lock(lock: Any):
    if lock is None:
        yield
        return
    try:
        with lock:
            yield
    except TypeError:
        yield


def _iter_signals(node: Any):
    if isinstance(node, dict):
        if any(key in node for key in ("trace_id", "traceId", "pair", "symbol", "direction", "engine_d", "engine_b")):
            yield node
        for value in node.values():
            yield from _iter_signals(value)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_signals(item)


def _runtime_candidates(runtime: Any) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    if runtime is None:
        return candidates
    for attr in ("last_scan_results", "get_last_scan_results"):
        fn = getattr(runtime, attr, None)
        if callable(fn):
            try:
                candidates.extend(dict(item) for item in _iter_signals(fn()) if isinstance(item, dict))
            except Exception:
                pass
    for attr in ("last_scan", "scan_cache", "signals_cache", "live_dashboard_scalp_cache"):
        cache = getattr(runtime, attr, None)
        lock = getattr(runtime, f"{attr}_lock", None)
        with _maybe_lock(lock):
            try:
                candidates.extend(dict(item) for item in _iter_signals(cache) if isinstance(item, dict))
            except Exception:
                pass
    return candidates


def _find_signal(
    signal_id: str | None = None,
    symbol: str | None = None,
    runtime: Any = None,
    signal: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    if isinstance(signal, dict) and signal:
        return dict(signal), {"found": True, "source": "provided_signal", "warnings": []}

    runtime = runtime if runtime is not None else _RUNTIME
    trace_text = str(signal_id or "").strip()
    symbol_text = str(symbol or "").strip().upper()
    warnings: list[str] = []
    candidates = _runtime_candidates(runtime)
    if runtime is None:
        warnings.append("runtime unavailable; signal lookup skipped")

    if trace_text:
        for candidate in candidates:
            if str(candidate.get("trace_id") or candidate.get("traceId") or "").strip() == trace_text:
                return candidate, {"found": True, "source": "runtime", "warnings": warnings}
    if symbol_text:
        for candidate in candidates:
            candidate_symbol = str(_symbol_from(candidate) or "").strip().upper()
            if candidate_symbol == symbol_text:
                return candidate, {"found": True, "source": "runtime", "warnings": warnings}
    return None, {
        "found": False,
        "source": "runtime",
        "warnings": warnings,
        "missing_fields": [name for name, value in (("signal_id", trace_text), ("symbol", symbol_text)) if not value],
    }


def _missing_signal_shape(signal_id: str | None, symbol: str | None, lookup: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "ai_tool.signal_detail.v1",
        "status": "not_found",
        "generated_at": _now_iso(),
        "found": False,
        "signal_id": signal_id,
        "symbol": symbol,
        "signal": None,
        "packet": None,
        "summary": None,
        "missing_fields": lookup.get("missing_fields") or [],
        "warnings": _as_list(lookup.get("warnings")) + ["Signal not found; no signal data was fabricated."],
        "advisory_only": True,
        "execution_allowed": False,
    }


def _minimal_packet(signal: dict[str, Any], user_question: str | None = None, warning: str | None = None) -> dict[str, Any]:
    """Build a conservative packet when richer Phase 1/2 builders are unavailable."""
    symbol = _symbol_from(signal) or "unknown"
    missing_risk = [
        key
        for key in ("rr", "min_rr", "sl", "tp", "entry", "position_size", "spread")
        if signal.get(key) is None and signal.get("rr1" if key == "rr" else key) is None
    ]
    packet = {
        "schema_version": "1.0",
        "trace_id": str(signal.get("trace_id") or signal.get("traceId") or ""),
        "symbol": symbol,
        "asset_type": str(signal.get("type") or signal.get("asset_type") or "unknown").lower(),
        "direction": signal.get("direction"),
        "requested_style": signal.get("style") or signal.get("requested_style") or "UNKNOWN",
        "engine_source": signal.get("engine_source") or signal.get("engine") or "unknown",
        "created_at": _now_iso(),
        "market_context": {
            "pair": signal.get("pair") or signal.get("display") or symbol,
            "session": signal.get("session") or signal.get("current_session"),
            "timeframe": signal.get("timeframe") or signal.get("tf"),
            "timestamp": signal.get("timestamp") or signal.get("ts"),
            "regime": signal.get("regime") or signal.get("regimeName"),
            "data_freshness": signal.get("dataFreshness") or signal.get("data_freshness"),
        },
        "market_intelligence": _as_dict(signal.get("market_intelligence")),
        "engine_a": _as_dict(signal.get("engine_a")),
        "engine_b": _as_dict(signal.get("engine_b") or signal.get("engine_b_overlay")),
        "engine_c": _as_dict(signal.get("engine_c")),
        "engine_d": _as_dict(signal.get("engine_d") or signal.get("scalp")),
        "vision": _as_dict(signal.get("vision") or signal.get("ai_vision")),
        "risk": {
            "rr": signal.get("rr1") if signal.get("rr1") is not None else signal.get("rr"),
            "min_rr": signal.get("min_rr"),
            "sl": signal.get("sl"),
            "tp": signal.get("tp1") if signal.get("tp1") is not None else signal.get("tp"),
            "entry": signal.get("entry") if signal.get("entry") is not None else signal.get("price"),
            "position_size": signal.get("position_size"),
            "spread": signal.get("spread_pips") if signal.get("spread_pips") is not None else signal.get("spread"),
            "fees": signal.get("fee_guard") or signal.get("spread_fees_guard"),
            "missing_fields": missing_risk,
        },
        "data_quality": {
            "freshness_status": signal.get("freshnessStatus") or signal.get("freshness_status"),
            "missing_fields": [] if signal.get("freshnessStatus") or signal.get("freshness_status") else ["freshness_status"],
        },
        "similar_outcomes": _as_dict(signal.get("similar_outcomes")) or {
            "examples": [],
            "aggregate_sample_size": 0,
            "win_rate": None,
            "avg_r": None,
            "profit_factor": None,
            "oos_decay": None,
            "reliability": "unavailable",
            "warning": None,
            "missing_fields": [],
        },
        "user_question": user_question,
        "context_completeness": {"overall_complete": 0.0},
        "deterministic_gates": {
            "trade": signal.get("trade"),
            "advisory_rule_trade_allowed": signal.get("advisory_rule_trade_allowed"),
            "execution_allowed": False,
        },
        "tool_warnings": [warning] if warning else [],
    }
    for section in ("engine_a", "engine_b", "engine_c", "engine_d", "vision"):
        packet[section].setdefault("missing_fields", [])
    return packet


def _packet_for(
    signal_id: str | None = None,
    symbol: str | None = None,
    runtime: Any = None,
    signal: dict[str, Any] | None = None,
    user_question: str | None = None,
    vision: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any]]:
    found, lookup = _find_signal(signal_id=signal_id, symbol=symbol, runtime=runtime, signal=signal)
    if found is None:
        return None, None, lookup
    try:
        packet = build_packet_for_signal(found, user_question=user_question, vision=vision if vision is not None else found.get("vision"))
    except BaseException as exc:
        try:
            packet = build_ai_review_packet(found, user_question=user_question, vision=vision if vision is not None else found.get("vision"))
            packet.setdefault("tool_warnings", []).append(f"orchestrator packet build failed: {exc}")
        except BaseException as fallback_exc:
            packet = _minimal_packet(
                found,
                user_question=user_question,
                warning=f"Phase packet builders unavailable: {exc}; fallback unavailable: {fallback_exc}",
            )
    return found, packet, lookup


def get_signal_detail(
    trace_id: str | None = None,
    symbol: str | None = None,
    runtime: Any = None,
    signal: dict[str, Any] | None = None,
    signal_id: str | None = None,
) -> dict[str, Any]:
    """Return a canonical signal packet without inventing missing signals."""
    signal_id = signal_id or trace_id
    found, packet, lookup = _packet_for(signal_id=signal_id, symbol=symbol, runtime=runtime, signal=signal)
    if found is None or packet is None:
        return _missing_signal_shape(signal_id, symbol, lookup)
    return {
        "schema_version": "ai_tool.signal_detail.v1",
        "status": "ok",
        "generated_at": _now_iso(),
        "found": True,
        "signal_id": packet.get("trace_id") or signal_id,
        "symbol": packet.get("symbol") or _symbol_from(found) or symbol,
        "signal": found,
        "packet": packet,
        "summary": summarize_trade_for_chat(packet),
        "missing_fields": [],
        "warnings": _as_list(lookup.get("warnings")) + _as_list(packet.get("tool_warnings")),
        "advisory_only": True,
        "execution_allowed": False,
    }


def get_market_intelligence_tool(
    symbol: str | None = None,
    asset_type: str | None = None,
    force_refresh: bool = False,
) -> dict[str, Any]:
    out = get_market_intelligence(symbol, asset_type, force_refresh=force_refresh)
    return {
        "schema_version": "ai_tool.market_intelligence.v1",
        "status": out.get("freshness_status") or "ok",
        "generated_at": _now_iso(),
        "symbol": symbol,
        "asset_type": asset_type,
        "market_intelligence": out,
        "advisory_only": True,
        "execution_allowed": False,
    }


def get_pair_context_tool(
    symbol: str | None = None,
    asset_type: str | None = None,
    lookback_days: int = 5,
) -> dict[str, Any]:
    context = build_pair_context(symbol or "unknown", asset_type, lookback_days=lookback_days)
    return {
        "schema_version": "ai_tool.pair_context.v1",
        "status": context.get("freshness_status") or "ok",
        "generated_at": _now_iso(),
        "symbol": context.get("symbol"),
        "asset_type": context.get("asset_type"),
        "pair_context": context,
        "advisory_only": True,
        "execution_allowed": False,
    }


def get_historical_analogues_tool(
    trace_id: str | None = None,
    symbol: str | None = None,
    runtime: Any = None,
    signal: dict[str, Any] | None = None,
    packet: dict[str, Any] | None = None,
    limit: int = 5,
    signal_id: str | None = None,
) -> dict[str, Any]:
    signal_id = signal_id or trace_id
    found, built_packet, lookup = _packet_for(signal_id=signal_id, symbol=symbol, runtime=runtime, signal=signal)
    payload = packet or built_packet or found or {"symbol": symbol}
    existing = _as_dict(_as_dict(payload).get("similar_outcomes"))
    if existing and (existing.get("examples") or int(existing.get("aggregate_sample_size") or 0) > 0):
        analogues = dict(existing)
    else:
        try:
            analogues = find_similar_setups(payload, limit=limit)
        except Exception as exc:
            analogues = {
                "examples": [],
                "aggregate_sample_size": 0,
                "win_rate": None,
                "avg_r": None,
                "profit_factor": None,
                "oos_decay": None,
                "reliability": "unavailable",
                "warning": f"Similar-setup lookup failed: {exc}",
                "missing_fields": [],
            }
    sample_size = int(analogues.get("aggregate_sample_size") or 0)
    probability_allowed = sample_size >= 20 and analogues.get("reliability") in {"weak", "moderate", "strong"}
    return {
        "schema_version": "ai_tool.historical_analogues.v1",
        "status": "ok" if found is not None or packet is not None else "not_found",
        "generated_at": _now_iso(),
        "found_signal": found is not None,
        "signal_id": _as_dict(packet or built_packet).get("trace_id") or signal_id,
        "symbol": _as_dict(packet or built_packet).get("symbol") or symbol,
        "analogues": analogues,
        "probability_claim_allowed": probability_allowed,
        "probability_claim": analogues.get("win_rate") if probability_allowed else None,
        "warnings": _as_list(lookup.get("warnings")),
        "advisory_only": True,
        "execution_allowed": False,
    }


def get_chart_vision_tool(
    trace_id: str | None = None,
    symbol: str | None = None,
    runtime: Any = None,
    signal: dict[str, Any] | None = None,
    vision: dict[str, Any] | None = None,
    signal_id: str | None = None,
) -> dict[str, Any]:
    signal_id = signal_id or trace_id
    found, packet, lookup = _packet_for(signal_id=signal_id, symbol=symbol, runtime=runtime, signal=signal, vision=vision)
    vision_ctx = _as_dict(packet.get("vision")) if packet else _as_dict(vision)
    allowed = bool(vision_ctx.get("allowed_for_execution_context", False))
    return {
        "schema_version": "ai_tool.chart_vision.v1",
        "status": "ok" if vision_ctx else "unavailable",
        "generated_at": _now_iso(),
        "found_signal": found is not None,
        "signal_id": (packet or {}).get("trace_id") or signal_id,
        "symbol": (packet or {}).get("symbol") or symbol,
        "vision": vision_ctx,
        "freshness_status": vision_ctx.get("freshness_status") or "unknown",
        "allowed_for_execution_context": allowed,
        "warnings": _as_list(lookup.get("warnings")),
        "advisory_only": True,
        "execution_allowed": False,
    }


def get_engine_context_tool(
    trace_id: str | None = None,
    symbol: str | None = None,
    runtime: Any = None,
    signal: dict[str, Any] | None = None,
    signal_id: str | None = None,
) -> dict[str, Any]:
    signal_id = signal_id or trace_id
    found, packet, lookup = _packet_for(signal_id=signal_id, symbol=symbol, runtime=runtime, signal=signal)
    engines = {
        "engine_a": _as_dict((packet or {}).get("engine_a")),
        "engine_b": _as_dict((packet or {}).get("engine_b")),
        "engine_c": _as_dict((packet or {}).get("engine_c")),
        "engine_d": _as_dict((packet or {}).get("engine_d")),
        "deterministic_gates": _as_dict((packet or {}).get("deterministic_gates")),
    }
    return {
        "schema_version": "ai_tool.engine_context.v1",
        "status": "ok" if found is not None else "not_found",
        "generated_at": _now_iso(),
        "found_signal": found is not None,
        "signal_id": (packet or {}).get("trace_id") or signal_id,
        "symbol": (packet or {}).get("symbol") or symbol,
        "engines": engines,
        "context_completeness": _as_dict((packet or {}).get("context_completeness")),
        "warnings": _as_list(lookup.get("warnings")),
        "advisory_only": True,
        "execution_allowed": False,
    }


def get_open_risk_state_tool(runtime: Any = None) -> dict[str, Any]:
    """Return best-effort read-only risk state from the supplied runtime only."""
    runtime = runtime if runtime is not None else _RUNTIME
    warnings: list[str] = []
    risk_state: dict[str, Any] = {}
    if runtime is None:
        warnings.append("runtime unavailable; open risk state not inspected")
    else:
        for attr in ("open_risk_state", "risk_state", "open_positions", "paper_positions"):
            value = getattr(runtime, attr, None)
            if callable(value):
                try:
                    value = value()
                except Exception as exc:
                    warnings.append(f"{attr} unavailable: {exc}")
                    continue
            if value is not None:
                risk_state[attr] = value
    return {
        "schema_version": "ai_tool.open_risk_state.v1",
        "status": "ok" if risk_state else "unavailable",
        "generated_at": _now_iso(),
        "risk_state": risk_state,
        "source": "runtime_only",
        "warnings": warnings,
        "advisory_only": True,
        "execution_allowed": False,
    }


def compare_symbols_tool(
    left_symbol: str | None = None,
    right_symbol: str | None = None,
    runtime: Any = None,
    left_signal: dict[str, Any] | None = None,
    right_signal: dict[str, Any] | None = None,
    primary_symbol: str | None = None,
    comparison_symbol: str | None = None,
    style: str | None = None,
) -> dict[str, Any]:
    left_symbol = left_symbol or primary_symbol
    right_symbol = right_symbol or comparison_symbol
    left = get_signal_detail(symbol=left_symbol, runtime=runtime, signal=left_signal)
    right = get_signal_detail(symbol=right_symbol, runtime=runtime, signal=right_signal)
    left_packet = _as_dict(left.get("packet"))
    right_packet = _as_dict(right.get("packet"))
    return {
        "schema_version": "ai_tool.compare_symbols.v1",
        "status": "ok" if left.get("found") or right.get("found") else "not_found",
        "generated_at": _now_iso(),
        "left": left,
        "right": right,
        "comparison": {
            "both_available": bool(left.get("found") and right.get("found")),
            "left_available": bool(left.get("found")),
            "right_available": bool(right.get("found")),
            "symbols": [left.get("symbol") or left_symbol, right.get("symbol") or right_symbol],
            "directions": [left_packet.get("direction"), right_packet.get("direction")],
            "completeness": [
                _as_dict(left_packet.get("context_completeness")).get("overall_complete"),
                _as_dict(right_packet.get("context_completeness")).get("overall_complete"),
            ],
        },
        "warnings": _as_list(left.get("warnings")) + _as_list(right.get("warnings")),
        "advisory_only": True,
        "execution_allowed": False,
    }


def get_strategist_view_tool(
    trace_id: str | None = None,
    symbol: str | None = None,
    runtime: Any = None,
    signal: dict[str, Any] | None = None,
    mode: str = "pre_trade",
    asset_scope: str = "all",
    lookback_days: int = 7,
    signal_id: str | None = None,
) -> dict[str, Any]:
    signal_id = signal_id or trace_id
    found, packet, lookup = _packet_for(signal_id=signal_id, symbol=symbol, runtime=runtime, signal=signal)
    if mode == "morning_brief":
        view = strategist_morning_brief(asset_scope)
    elif mode == "weekly":
        view = weekly_strategy_retrospective(lookback_days)
    else:
        view = strategist_pre_trade_check(packet or {})
    return {
        "schema_version": "ai_tool.strategist_view.v1",
        "status": "ok",
        "generated_at": _now_iso(),
        "mode": mode,
        "found_signal": found is not None,
        "signal_id": (packet or {}).get("trace_id") or signal_id,
        "symbol": (packet or {}).get("symbol") or symbol,
        "view": view,
        "warnings": _as_list(lookup.get("warnings")),
        "advisory_only": True,
        "execution_allowed": False,
    }


def get_facts_used_tool(
    trace_id: str | None = None,
    symbol: str | None = None,
    runtime: Any = None,
    signal: dict[str, Any] | None = None,
    user_question: str | None = None,
    signal_id: str | None = None,
) -> dict[str, Any]:
    signal_id = signal_id or trace_id
    found, packet, lookup = _packet_for(
        signal_id=signal_id,
        symbol=symbol,
        runtime=runtime,
        signal=signal,
        user_question=user_question,
    )
    summary = summarize_trade_for_chat(packet or {}) if packet else {"facts_used": [], "missing_data": []}
    decision = run_ai_trade_review(packet, mode="tool") if packet else {}
    return {
        "schema_version": "ai_tool.facts_used.v1",
        "status": "ok" if found is not None else "not_found",
        "generated_at": _now_iso(),
        "found_signal": found is not None,
        "signal_id": (packet or {}).get("trace_id") or signal_id,
        "symbol": (packet or {}).get("symbol") or symbol,
        "facts_used": summary.get("facts_used") or [],
        "missing_data": summary.get("missing_data") or [],
        "decision": decision.get("decision"),
        "confidence_type": decision.get("confidence_type"),
        "warnings": _as_list(lookup.get("warnings")),
        "advisory_only": True,
        "execution_allowed": False,
    }


def get_latest_research_summary_tool(
    limit: int = 5,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Load and summarize the latest research results."""
    try:
        from athena_research.research_agent import (
            load_latest_research_results,
            summarize_research_results,
        )
        if run_id:
            summary = summarize_research_results(run_id)
        else:
            results = load_latest_research_results(limit=limit)
            if results and not results[0].get("warning"):
                summary = summarize_research_results()
            else:
                summary = {
                    "warning": "No research results found.",
                    "missing_fields": ["research_results"],
                }
    except ImportError as exc:
        summary = {
            "warning": f"Research Agent module unavailable: {exc}",
            "missing_fields": ["research_agent_module"],
        }
    except Exception as exc:
        summary = {
            "warning": f"Failed to load research results: {exc}",
            "missing_fields": [],
        }
    return {
        "schema_version": "ai_tool.research_summary.v1",
        "status": "ok" if not summary.get("warning") else "unavailable",
        "generated_at": _now_iso(),
        "research_summary": summary,
        "advisory_only": True,
        "execution_allowed": False,
    }


def propose_research_plan_tool(
    user_goal: str | None = None,
    scope: dict[str, Any] | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Propose a research plan based on latest results and user goal."""
    try:
        from athena_research.research_agent import (
            propose_next_research_plan,
            summarize_research_results,
        )
        scope = scope if isinstance(scope, dict) else {}
        summary = summarize_research_results(
            run_id or scope.get("run_id") or scope.get("run_path_or_id")
        )
        plan = propose_next_research_plan(summary, user_goal=user_goal)
        status = "proposed"
    except ImportError as exc:
        plan = {"warning": f"Research Agent module unavailable: {exc}"}
        status = "unavailable"
    except Exception as exc:
        plan = {"warning": f"Failed to propose plan: {exc}"}
        status = "error"
    return {
        "schema_version": "ai_tool.research_plan.v1",
        "status": status,
        "generated_at": _now_iso(),
        "plan": plan,
        "advisory_only": True,
        "execution_allowed": False,
    }


def validate_research_plan_tool(plan: dict[str, Any] | None = None) -> dict[str, Any]:
    """Validate a research plan for safety and consistency."""
    if not isinstance(plan, dict):
        return {
            "schema_version": "ai_tool.validate_research_plan.v1",
            "status": "error",
            "generated_at": _now_iso(),
            "validation": {"valid": False, "issues": ["No plan provided."]},
            "advisory_only": True,
            "execution_allowed": False,
        }
    try:
        from athena_research.research_agent import validate_research_plan
        validation = validate_research_plan(plan)
    except ImportError as exc:
        validation = {"valid": False, "issues": [f"Module unavailable: {exc}"]}
    except Exception as exc:
        validation = {"valid": False, "issues": [f"Validation error: {exc}"]}
    return {
        "schema_version": "ai_tool.validate_research_plan.v1",
        "status": "ok",
        "generated_at": _now_iso(),
        "validation": validation,
        "advisory_only": True,
        "execution_allowed": False,
    }


def compare_research_runs_tool(
    baseline: str | None = None,
    candidate: str | None = None,
) -> dict[str, Any]:
    """Compare two research runs."""
    if not baseline or not candidate:
        return {
            "schema_version": "ai_tool.compare_research_runs.v1",
            "status": "error",
            "generated_at": _now_iso(),
            "comparison": {
                "missing_fields": [k for k, v in [("baseline", baseline), ("candidate", candidate)] if not v],
            },
            "advisory_only": True,
            "execution_allowed": False,
        }
    try:
        from athena_research.research_agent import compare_research_runs
        comparison = compare_research_runs(baseline, candidate)
    except ImportError as exc:
        comparison = {"warning": f"Module unavailable: {exc}"}
    except Exception as exc:
        comparison = {"warning": f"Comparison error: {exc}"}
    return {
        "schema_version": "ai_tool.compare_research_runs.v1",
        "status": "ok",
        "generated_at": _now_iso(),
        "baseline_run_id": baseline,
        "candidate_run_id": candidate,
        "comparison": comparison,
        "advisory_only": True,
        "execution_allowed": False,
    }


def get_research_recommendations_tool(
    run_id: str | None = None,
    summary_or_comparison: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Generate recommendations from research results."""
    try:
        from athena_research.research_agent import (
            produce_research_recommendations,
            summarize_research_results,
        )
        if run_id:
            summary = summarize_research_results(run_id)
        elif isinstance(summary_or_comparison, dict):
            summary = summary_or_comparison
        else:
            summary = summarize_research_results()
        recommendations = produce_research_recommendations(summary)
    except ImportError as exc:
        recommendations = [{"warning": f"Module unavailable: {exc}"}]
    except Exception as exc:
        recommendations = [{"warning": f"Error: {exc}"}]
    return {
        "schema_version": "ai_tool.research_recommendations.v1",
        "status": "ok",
        "generated_at": _now_iso(),
        "recommendations": recommendations,
        "advisory_only": True,
        "execution_allowed": False,
    }


def get_ai_tool_registry() -> dict[str, Callable[..., dict[str, Any]]]:
    return {
        "get_signal_detail": get_signal_detail,
        "get_market_intelligence_tool": get_market_intelligence_tool,
        "get_pair_context_tool": get_pair_context_tool,
        "get_historical_analogues_tool": get_historical_analogues_tool,
        "get_chart_vision_tool": get_chart_vision_tool,
        "get_engine_context_tool": get_engine_context_tool,
        "get_open_risk_state_tool": get_open_risk_state_tool,
        "compare_symbols_tool": compare_symbols_tool,
        "get_strategist_view_tool": get_strategist_view_tool,
        "get_facts_used_tool": get_facts_used_tool,
        "get_latest_research_summary_tool": get_latest_research_summary_tool,
        "propose_research_plan_tool": propose_research_plan_tool,
        "validate_research_plan_tool": validate_research_plan_tool,
        "compare_research_runs_tool": compare_research_runs_tool,
        "get_research_recommendations_tool": get_research_recommendations_tool,
    }
