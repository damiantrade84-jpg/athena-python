"""Read-only AI Trading Agent chat route."""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterable

from flask import jsonify, request

from ai_conversation_store import get_recent_turns, list_threads, set_thread_title
from ai_strategist import strategist_morning_brief, strategist_pre_trade_check, weekly_strategy_retrospective
from ai_tools import set_ai_tools_runtime
from ai_trade_chat import run_trade_chat_turn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _session_id(value: Any) -> str:
    text = str(value or "").strip()
    return text or f"ai-chat-{uuid.uuid4().hex}"


def _safe_json(value: Any) -> str:
    try:
        return json.dumps(value, default=str)
    except Exception:
        return "{}"


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


def _init_conversation_db(db_path: str, log: Any = None) -> None:
    if not db_path:
        return
    try:
        directory = os.path.dirname(os.path.abspath(db_path))
        if directory:
            os.makedirs(directory, exist_ok=True)
        with sqlite3.connect(db_path, timeout=15.0) as con:
            con.execute("PRAGMA journal_mode=WAL")
            con.execute(
                """CREATE TABLE IF NOT EXISTS ai_conversations (
                    thread_id TEXT PRIMARY KEY,
                    pair TEXT,
                    trace_id TEXT,
                    created_ts TEXT,
                    last_ts TEXT
                )"""
            )
            con.execute(
                """CREATE TABLE IF NOT EXISTS ai_conversation_turns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    thread_id TEXT,
                    role TEXT,
                    content TEXT,
                    tool_calls TEXT,
                    ts TEXT
                )"""
            )
            con.commit()
    except Exception as exc:
        if log:
            try:
                log.warning("[AI_AGENT] conversation DB init failed: %s", exc)
            except Exception:
                pass


def _persist_turns(db_path: str, session_id: str, symbol: str | None, trace_id: str | None, message: str, answer: dict[str, Any], log: Any = None) -> None:
    if not db_path:
        return
    try:
        _init_conversation_db(db_path, log)
        ts = _now()
        with sqlite3.connect(db_path, timeout=15.0) as con:
            con.execute("PRAGMA journal_mode=WAL")
            con.execute(
                """INSERT INTO ai_conversations(thread_id, pair, trace_id, created_ts, last_ts)
                   VALUES(?,?,?,?,?)
                   ON CONFLICT(thread_id) DO UPDATE SET
                       pair=excluded.pair,
                       trace_id=excluded.trace_id,
                       last_ts=excluded.last_ts""",
                (session_id, symbol, trace_id, ts, ts),
            )
            con.execute(
                "INSERT INTO ai_conversation_turns(thread_id, role, content, tool_calls, ts) VALUES(?,?,?,?,?)",
                (session_id, "user", message, "[]", ts),
            )
            con.execute(
                "INSERT INTO ai_conversation_turns(thread_id, role, content, tool_calls, ts) VALUES(?,?,?,?,?)",
                (session_id, "assistant", answer.get("answer") or "", _safe_json(answer), ts),
            )
            con.commit()
    except Exception as exc:
        if log:
            try:
                log.warning("[AI_AGENT] conversation persist failed: %s", exc)
            except Exception:
                pass


def _iter_signals(node: Any) -> Iterable[dict[str, Any]]:
    if isinstance(node, dict):
        if any(key in node for key in ("trace_id", "pair", "symbol", "direction", "engine_d", "engine_b")):
            yield node
        for value in node.values():
            yield from _iter_signals(value)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_signals(item)


def _find_signal(runtime: Any, trace_id: str | None, symbol: str | None) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []
    try:
        last_scan = runtime.last_scan_results() if callable(getattr(runtime, "last_scan_results", None)) else {}
        candidates.extend(_iter_signals(last_scan))
    except Exception:
        pass
    cache = getattr(runtime, "live_dashboard_scalp_cache", None)
    with _maybe_lock(getattr(runtime, "live_dashboard_scalp_cache_lock", None)):
        if isinstance(cache, dict):
            candidates.extend(_iter_signals(cache))

    trace_text = str(trace_id or "").strip()
    symbol_text = str(symbol or "").strip().upper()
    if trace_text:
        for signal in candidates:
            if str(signal.get("trace_id") or signal.get("traceId") or "").strip() == trace_text:
                return dict(signal)
    if symbol_text:
        for signal in candidates:
            sig_symbol = str(signal.get("symbol") or signal.get("pair") or signal.get("display") or "").strip().upper()
            if sig_symbol == symbol_text:
                return dict(signal)
    return None


def _answer_text(symbol: str | None, message: str, decision: dict[str, Any], summary: dict[str, Any]) -> str:
    facts = summary.get("facts_used") or []
    missing = summary.get("missing_data") or []
    head = f"AI Trading Agent review for {symbol or 'the selected setup'}."
    if not facts:
        return f"{head} I do not have enough linked signal data yet. Select a signal or provide a trace_id for a specific review."
    mi = _as_dict(summary.get("market_intelligence"))
    vision = _as_dict(summary.get("vision_summary"))
    mi_line = "Market intelligence is unavailable." if mi.get("freshness_status") in {None, "unavailable"} else f"Market intelligence freshness: {mi.get('freshness_status')}."
    vision_line = "Vision is missing or not usable for execution context." if not vision.get("allowed_for_execution_context") else "Vision is fresh enough for advisory context."
    sections = {
        "Market read": decision.get("market_read") or mi_line,
        "Trade thesis": decision.get("thesis") or "Advisory review only.",
        "What supports the setup": "; ".join(decision.get("supports") or []) or "No complete support set available.",
        "What argues against it": "; ".join(decision.get("contradictions") or []) or "No deterministic contradictions reported.",
        "What would confirm the trade": "; ".join(decision.get("confirmation_needed") or []) or "Fresh deterministic gates and risk checks must remain clean.",
        "What would invalidate the trade": decision.get("invalidation") or "Use deterministic signal invalidation.",
        "Historical analogue summary": decision.get("historical_evidence_summary") or "Historical scaffold unavailable.",
        "Risk/fee/spread warning": "; ".join(decision.get("risk_notes") or []) or "No extra risk note beyond deterministic gates.",
        "Vision context": vision_line,
        "Final action": decision.get("final_action") or "advisory_review_only",
    }
    parts = [head, f"Decision card: {decision.get('decision')}.", mi_line]
    parts.extend(f"{title}: {body}" for title, body in sections.items())
    if decision.get("contradictions"):
        parts.append("Contradictions detected: " + ", ".join(decision.get("contradictions") or []))
    if missing:
        parts.append("Missing data: " + ", ".join(missing[:8]))
    if "EXECUTE" in str(message or "").upper():
        parts.append("This chat is advisory/read-only and cannot place, approve, or route trades.")
    return " ".join(part for part in parts if part)


def register_ai_agent_routes(app, runtime) -> None:
    """Register read-only AI Agent chat endpoints."""

    log = getattr(runtime, "log", None)
    audit_db = str(getattr(runtime, "AUDIT_DB", "") or "")
    _init_conversation_db(audit_db, log)
    set_ai_tools_runtime(runtime)

    @app.post("/api/ai/trade-chat")
    def api_ai_trade_chat():
        payload = request.get_json(silent=True) or {}
        trace_id = str(payload.get("trace_id") or "").strip() or None
        symbol = str(payload.get("symbol") or "").strip() or None
        message = str(payload.get("message") or "").strip()
        if not message:
            return jsonify({"error": "message_required"}), 400

        signal_payload = payload.get("signal")
        signal_payload = signal_payload if isinstance(signal_payload, dict) else None

        answer = run_trade_chat_turn(
            {
                "session_id": payload.get("session_id"),
                "trace_id": trace_id,
                "symbol": symbol,
                "message": message,
                "include_vision": payload.get("include_vision", True),
                "include_similar_setups": payload.get("include_similar_setups", True),
                "comparison_symbol": payload.get("comparison_symbol") or payload.get("compare_symbol"),
                "signal": signal_payload,
                "_audit_db": audit_db,
            }
        )
        json_safe = getattr(runtime, "json_safe", None)
        safe_answer = json_safe(answer) if callable(json_safe) else answer
        return jsonify(safe_answer)

    @app.get("/api/ai/conversations")
    def api_ai_conversations():
        symbol = str(request.args.get("symbol") or "").strip() or None
        try:
            limit = int(request.args.get("limit") or 50)
        except (TypeError, ValueError):
            limit = 50
        return jsonify({"conversations": list_threads(symbol=symbol, limit=limit, db_path=audit_db)})

    @app.get("/api/ai/conversations/<thread_id>")
    def api_ai_conversation(thread_id: str):
        try:
            limit = int(request.args.get("limit") or 50)
        except (TypeError, ValueError):
            limit = 50
        return jsonify({"thread_id": thread_id, "turns": get_recent_turns(thread_id, limit=limit, db_path=audit_db)})

    @app.post("/api/ai/conversations/<thread_id>/title")
    def api_ai_conversation_title(thread_id: str):
        payload = request.get_json(silent=True) or {}
        title = str(payload.get("title") or "").strip()
        if not title:
            return jsonify({"error": "title_required"}), 400
        ok = set_thread_title(thread_id, title, db_path=audit_db)
        return jsonify({"ok": ok, "thread_id": thread_id})

    @app.get("/api/ai/strategist/brief")
    def api_ai_strategist_brief():
        asset_scope = str(request.args.get("asset_scope") or request.args.get("scope") or "all")
        return jsonify(strategist_morning_brief(asset_scope))

    @app.post("/api/ai/strategist/pre-trade-check")
    def api_ai_strategist_pre_trade_check():
        payload = request.get_json(silent=True) or {}
        packet = payload.get("packet") if isinstance(payload.get("packet"), dict) else payload
        return jsonify(strategist_pre_trade_check(packet))

    @app.get("/api/ai/strategist/weekly-retrospective")
    def api_ai_strategist_weekly_retrospective():
        try:
            lookback_days = int(request.args.get("lookback_days") or 7)
        except (TypeError, ValueError):
            lookback_days = 7
        return jsonify(weekly_strategy_retrospective(lookback_days))
