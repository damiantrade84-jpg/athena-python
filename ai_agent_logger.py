"""Compact best-effort audit logging for AI Agent chat turns."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Any


def _hash_text(value: Any) -> str:
    data = str(value or "").encode("utf-8", errors="ignore")
    return hashlib.sha256(data).hexdigest()


def _safe_json(value: Any) -> str:
    try:
        return json.dumps(value, default=str, separators=(",", ":"))
    except Exception:
        return "{}"


def _log_path() -> str:
    return os.environ.get("ATHENA_AI_AGENT_LOG") or os.path.join(
        os.path.dirname(__file__),
        "logs",
        "ai_review",
        "ai_agent_chat.jsonl",
    )


def log_ai_chat_turn(
    *,
    thread_id: str | None,
    trace_id: str | None,
    symbol: str | None,
    user_message: str,
    assistant_answer: str,
    tools_called: list[dict[str, Any]] | None = None,
    facts_used: list[str] | None = None,
    decision: str | None = None,
    safety_flags: list[str] | None = None,
    missing_data: list[str] | None = None,
    contradiction_flags: list[str] | None = None,
    model_used: str | None = None,
    prompt: str | None = None,
) -> bool:
    """Append a compact log row. Logging failure is intentionally non-fatal."""
    try:
        path = _log_path()
        directory = os.path.dirname(os.path.abspath(path))
        if directory:
            os.makedirs(directory, exist_ok=True)
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "thread_id": thread_id,
            "trace_id": trace_id,
            "symbol": symbol,
            "user_message_hash": _hash_text(user_message),
            "assistant_answer_hash": _hash_text(assistant_answer),
            "tools_called": tools_called or [],
            "facts_used": facts_used or [],
            "decision": decision,
            "safety_flags": safety_flags or [],
            "missing_data": missing_data or [],
            "contradiction_flags": contradiction_flags or [],
            "model_used": model_used or "deterministic_fallback",
            "prompt_hash": _hash_text(prompt) if prompt else None,
        }
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(_safe_json(record) + "\n")
        return True
    except Exception:
        return False
