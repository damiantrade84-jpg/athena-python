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


def build_marcus_review_incomplete_result(
    signal: dict,
    *,
    reason: str,
    provider: str = "",
    selected_provider: str = "",
    model: str = "",
    fallback_used: bool = False,
) -> dict[str, Any]:
    """Fail-closed Marcus text-review result for empty or unparseable provider output."""
    pair = str(
        signal.get("pair") or signal.get("display") or signal.get("symbol") or "UNKNOWN"
    )
    symbol = str(signal.get("symbol") or pair.replace("/", ""))
    direction = str(signal.get("direction") or "").strip().lower()
    if direction not in ("long", "short"):
        direction = "neutral"
    style = str(
        signal.get("style")
        or signal.get("tradeStyle")
        or signal.get("trade_style")
        or "unknown"
    )
    style_upper = style.upper() if style else "UNKNOWN"
    timeframe = str(
        signal.get("timeframe")
        or signal.get("tf")
        or signal.get("execution_tf")
        or signal.get("context_tf")
        or "unknown"
    )
    entry = signal.get("price") or signal.get("entry") or "unknown"
    sl = signal.get("sl") or signal.get("stop") or signal.get("stopLoss") or "unknown"
    tp = signal.get("tp1") or signal.get("tp") or signal.get("takeProfit") or "unknown"
    warning = f"AI review incomplete: {reason}. No AI approval was produced."
    style_rating = {"grade": "F", "edgeProbability": 0, "riskLevel": "High"}

    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "bias": direction,
        "setup_type": "review_incomplete",
        "trend_score": 0,
        "structure_score": 0,
        "momentum_score": 0,
        "liquidity_score": 0,
        "risk_score": 0,
        "confirmation_score": 0,
        "total_score": 0,
        "grade": "F",
        "ai_action": "reject",
        "blocking_reasons": ["ai_review_incomplete"],
        "reason": warning,
        "verdict": "AI review incomplete; do not treat this as advisory approval.",
        "narrative": (
            f"{pair} {direction.upper()} could not be reviewed because the AI provider "
            "returned no parseable JSON. Deterministic Athena gates remain authoritative."
        ),
        "entryZone": str(entry),
        "invalidation": str(sl),
        "keyLevels": f"entry={entry} sl={sl} tp={tp}",
        "positionSizing": "No AI sizing recommendation",
        "tradeStyle": style_upper,
        "tradeStyleReason": "AI review did not complete.",
        "warnings": [warning],
        "edgeProbability": 0,
        "riskLevel": "High",
        "style_ratings": {
            "scalp": dict(style_rating),
            "intraday": dict(style_rating),
            "swing": dict(style_rating),
        },
        "parse_success": False,
        "provider": provider,
        "selectedProvider": selected_provider or provider,
        "model": model,
        "fallbackUsed": bool(fallback_used),
        "fallback_used": bool(fallback_used),
    }
