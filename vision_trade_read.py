"""Structured VisionTradeRead parsing and freshness policy helpers."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any


RIGHT_EDGE_VALUES = {"CONFIRMS", "REVIEW", "POTENTIAL_REVERSAL", "UNKNOWN"}
TF_ALIGNMENT_VALUES = {"ALIGNED", "CONFLICTED", "UNKNOWN"}
STYLE_VALUES = {"STRONG", "MODERATE", "WEAK", "AVOID", "CONTRADICTS", "NA"}


def _default_policy() -> dict[str, Any]:
    try:
        from config import CONFIG

        return CONFIG.get("VISION_FRESHNESS_POLICY") or {}
    except BaseException:
        return {
            "max_age_sec": {
                "M1": 180,
                "M5": 900,
                "M15": 1800,
                "H1": 5400,
                "H4": 18000,
                "D1": 97200,
            }
        }


def _extract_json_object(text: str) -> dict[str, Any] | None:
    raw = text or ""
    if "```" in raw:
        for part in raw.split("```"):
            part = part.strip()
            if part.lower().startswith("json"):
                part = part[4:].strip()
            if part.startswith("{"):
                try:
                    return json.loads(part)
                except Exception:
                    pass
    start = raw.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(raw)):
        ch = raw[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
        else:
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(raw[start : i + 1])
                    except Exception:
                        return None
    return None


def _norm(value: Any, allowed: set[str], fallback: str) -> str:
    text = str(value or "").strip().upper().replace(" ", "_")
    return text if text in allowed else fallback


def _footer_structured(text: str) -> dict[str, Any]:
    up = (text or "").upper()
    right = "UNKNOWN"
    m = re.search(r"RIGHT\s+EDGE\s*:\s*(CONFIRMS|REVIEW|POTENTIAL\s+REVERSAL)", up)
    if m:
        right = m.group(1).replace(" ", "_")
    align = "UNKNOWN"
    m = re.search(r"TF\s+ALIGNMENT\s*:\s*(ALIGNED|CONFLICTED)", up)
    if m:
        align = m.group(1)
    ratings: dict[str, str] = {}
    for style in ("SCALP", "INTRADAY", "SWING"):
        sm = re.search(rf"{style}\s+RATING\s*:\s*(STRONG|MODERATE|WEAK|AVOID|CONTRADICTS?)", up)
        ratings[style.lower()] = _norm(sm.group(1) if sm else None, STYLE_VALUES, "NA")
    return {"right_edge_status": right, "tf_alignment": align, "style_ratings": ratings}


def _parse_dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value) / (1000 if float(value) > 10_000_000_000 else 1), tz=timezone.utc)
        except Exception:
            return None
    text = str(value).strip()
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def apply_vision_freshness_policy(read: dict[str, Any], timeframe: str | None = None, policy: dict[str, Any] | None = None) -> dict[str, Any]:
    out = dict(read or {})
    policy = policy or _default_policy()
    warnings = list(out.get("warnings") or [])
    tf = str(timeframe or out.get("timeframe") or "H4").upper()
    max_age = (policy.get("max_age_sec") or {}).get(tf)
    chart_ts = _parse_dt(out.get("chart_timestamp"))
    latest_ts = _parse_dt(out.get("latest_candle_ts"))
    freshness = out.get("freshness_status") or "unknown"
    allowed = bool(out.get("allowed_for_execution_context", False))

    if not chart_ts:
        freshness = "unknown"
        allowed = False
        warnings.append("chart_timestamp missing; Vision downgraded to review-only.")
    if not latest_ts:
        freshness = "unknown"
        allowed = False
        warnings.append("latest_candle_ts missing; Vision cannot be used for execution context.")
    if latest_ts and max_age is not None:
        age = (datetime.now(timezone.utc) - latest_ts).total_seconds()
        if age > float(max_age):
            freshness = "stale"
            allowed = False
            warnings.append(f"latest_candle_ts is stale for {tf}; age_sec={int(age)} max_age_sec={max_age}.")
        elif chart_ts:
            freshness = "fresh"
            allowed = True
    out["freshness_status"] = freshness
    out["allowed_for_execution_context"] = allowed
    out["warnings"] = sorted(set(str(w) for w in warnings if w))
    return out


def parse_vision_trade_read(
    analysis_text: str,
    *,
    structured: dict[str, Any] | None = None,
    chart_timestamp: Any = None,
    latest_candle_ts: Any = None,
    image_hash: str | None = None,
    timeframe: str | None = None,
) -> dict[str, Any]:
    payload = _extract_json_object(analysis_text) or {}
    if payload.get("schema_version") != "vision_trade_read.v1":
        payload = {}
    fallback = dict(structured or {}) or _footer_structured(analysis_text)
    style = payload.get("style_ratings") or fallback.get("style_ratings") or {}
    read = {
        "schema_version": "vision_trade_read.v1",
        "right_edge_status": _norm(payload.get("right_edge_status") or fallback.get("right_edge_status"), RIGHT_EDGE_VALUES, "UNKNOWN"),
        "tf_alignment": _norm(payload.get("tf_alignment") or fallback.get("tf_alignment"), TF_ALIGNMENT_VALUES, "UNKNOWN"),
        "confirms_direction": payload.get("confirms_direction", fallback.get("confirms_direction")),
        "chart_identity_confidence": payload.get("chart_identity_confidence") or "from_request",
        "freshness_status": payload.get("freshness_status") or "unknown",
        "allowed_for_execution_context": bool(payload.get("allowed_for_execution_context", False)),
        "last_candle_read": payload.get("last_candle_read") or {},
        "last_3_5_candles": payload.get("last_3_5_candles") or {},
        "visible_structure": payload.get("visible_structure") or {"support": [], "resistance": [], "trendline_or_channel": "unknown", "obstacles_before_tp": []},
        "style_ratings": {
            "scalp": _norm(style.get("scalp"), STYLE_VALUES, "NA"),
            "intraday": _norm(style.get("intraday"), STYLE_VALUES, "NA"),
            "swing": _norm(style.get("swing"), STYLE_VALUES, "NA"),
        },
        "level_suggestions": payload.get("level_suggestions") or {
            "sl": {"action": "UNKNOWN", "suggested": None, "reason": "No structured Vision level suggestion."},
            "tp1": {"action": "UNKNOWN", "suggested": None, "reason": "No structured Vision level suggestion."},
        },
        "memo": payload.get("memo") or (analysis_text or "")[:800],
        "missing_visual_information": list(payload.get("missing_visual_information") or []),
        "warnings": list(payload.get("warnings") or []),
        "chart_timestamp": chart_timestamp,
        "latest_candle_ts": latest_candle_ts,
        "image_hash": image_hash,
    }
    return apply_vision_freshness_policy(read, timeframe=timeframe)


def build_vision_cache_key(
    *,
    symbol: str,
    timeframe_set: str,
    direction: str | None,
    trace_id: str | None,
    latest_candle_ts: Any,
    image_hash: str | None,
) -> dict[str, Any]:
    parts = [
        symbol or "unknown",
        timeframe_set or "unknown",
        direction or "unknown",
        trace_id or "ui_only",
        str(latest_candle_ts or "unknown"),
        image_hash or "no_image_hash",
    ]
    raw = "|".join(parts)
    return {
        "key": hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24],
        "raw": raw,
        "ui_only": not bool(trace_id and image_hash and latest_candle_ts),
    }
