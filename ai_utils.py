"""Shared AI parsing and normalization utilities."""

from __future__ import annotations

import json
import re
from typing import Any


def build_freshness_ai_context(signal: dict) -> str:
    """Build a structured freshness context string for AI prompts.

    Reads candleFetchMeta (per-TF staleness) and dataFreshness (gate decision)
    from a signal dict and returns a formatted multi-line string.
    Returns empty string when freshness data is absent — never raises.
    """
    try:
        meta: dict[str, Any] = signal.get("candleFetchMeta") or {}
        gate: dict[str, Any] = signal.get("dataFreshness") or {}

        if not meta and not gate:
            return ""

        lines = ["=== CANDLE DATA FRESHNESS ==="]

        # Per-timeframe bar timestamps and staleness
        tf_order = ["D1", "H4", "H1", "M15", "M5", "M1"]
        tf_lines = []
        for tf in tf_order:
            tf_data = meta.get(tf) or meta.get(tf.lower())
            if not tf_data:
                continue
            last_ts = tf_data.get("lastBarTime") or tf_data.get("last_bar_time") or "unknown"
            severity = tf_data.get("stalenessSeverity") or tf_data.get("staleness_severity") or "unknown"
            stale_flag = tf_data.get("lastBarStale") or tf_data.get("last_bar_stale")
            has_bucket = tf_data.get("hasCurrentBucket")
            age_sec = tf_data.get("lastBarAgeSec") or tf_data.get("last_bar_age_sec")

            parts = [f"{tf}: last_bar={last_ts} staleness={severity}"]
            if stale_flag:
                parts.append("STALE")
            if has_bucket is False:
                parts.append("no_current_bucket")
            if age_sec is not None:
                parts.append(f"age={age_sec}s")
            tf_lines.append(" | ".join(parts))

        if tf_lines:
            lines.append("Per-TF candle state:")
            lines.extend(f"  {l}" for l in tf_lines)
        else:
            lines.append("Per-TF candle state: no metadata available")

        # Data gate decision
        if gate:
            allowed = gate.get("allowed", False)
            blocked = gate.get("blocked") or []
            warnings = gate.get("warnings") or []
            reason = gate.get("reason") or ""
            block_on_stale = gate.get("blockExecutionOnStale")

            lines.append(f"Gate decision: {'ALLOWED' if allowed else 'BLOCKED'}")
            if reason:
                lines.append(f"  Reason: {reason}")
            if blocked:
                lines.append(f"  Blocked TFs: {', '.join(str(b) for b in blocked)}")
            if warnings:
                lines.append(f"  Warnings: {'; '.join(str(w) for w in warnings)}")
            if block_on_stale is not None:
                lines.append(f"  blockExecutionOnStale: {block_on_stale}")
        else:
            lines.append("Gate decision: not available")

        lines.append(
            "NOTE: If any candle bar is marked STALE or gate is BLOCKED, "
            "acknowledge data quality in your analysis."
        )

        return "\n".join(lines)
    except Exception:
        return ""


def parse_json_object(text: str) -> dict | None:
    """Parse a JSON object from LLM output with robust fallbacks."""
    if not text:
        return None

    # 1. Direct parse (works when response_format=json_object is used)
    try:
        result = json.loads(text)
        if isinstance(result, dict):
            return result
    except json.JSONDecodeError:
        pass

    # 2. Strip markdown code fences
    if "```" in text:
        for part in text.split("```"):
            part = part.strip()
            if part.startswith("json"):
                part = part[4:].strip()
            if part.startswith("{"):
                try:
                    return json.loads(part)
                except json.JSONDecodeError:
                    pass

    # 3. Extract first {...} block (handles any nesting depth)
    start = text.find("{")
    end = text.rfind("}") + 1
    if start >= 0 and end > start:
        try:
            result = json.loads(text[start:end])
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            pass

    return None

