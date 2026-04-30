"""ai_signal_trace.py — correlate AI surfaces per signal (Audit HIGH-010)."""

from __future__ import annotations

import time
import uuid


def generate_signal_trace_id() -> str:
    """Time-sortable ID when uuid7 exists; else timestamp-prefixed uuid4."""
    try:
        gen = getattr(uuid, "uuid7", None)
        if callable(gen):
            return str(gen())
    except Exception:
        pass
    return f"{int(time.time() * 1000):013d}-{uuid.uuid4().hex}"


def ensure_trace_id(signal: dict | None) -> dict | None:
    """Attach trace_id to signal dict if missing."""
    if not isinstance(signal, dict):
        return signal
    if not signal.get("trace_id"):
        signal["trace_id"] = generate_signal_trace_id()
    return signal
