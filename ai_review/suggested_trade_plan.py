"""Sanitize advisory-only suggested trade plans from AI chart review responses."""

from __future__ import annotations

from typing import Any

from timeframe_policy import TIMEFRAME_LADDER

SCHEMA_VERSION = "suggested_trade_plan.v1"

_VALID_DIRECTIONS = frozenset({"LONG", "SHORT"})
_VALID_ACTIONS = frozenset({"WAIT_FOR_LEVEL", "WAIT_FOR_ZONE", "NO_TRADE", "ENTRY_NOW", "WATCH_ONLY"})
_VALID_WATCH_ACTIONS = frozenset({"WAIT_FOR_LEVEL", "WAIT_FOR_ZONE", "WATCH_ONLY"})
_VALID_TRIGGERS = frozenset({
    "ACCEPTANCE_ABOVE",
    "ACCEPTANCE_BELOW",
    "PULLBACK_TO_ZONE",
    "REJECTION_FROM_ZONE",
    "SWEEP_RECLAIM",
})
_VALID_SOURCES = frozenset(
    {
        "ai_chart_review",
        "ai_scalp_chart_review",
        "engine_b_hotbench",
        "engine_b_candidate",
    }
)
_SCALP_SOURCES = frozenset({"ai_scalp_chart_review"})
_VALID_SCALP_CONTEXT_TF = frozenset({"M5", "M15"})
_VALID_SCALP_ENTRY_EXEC_TF = frozenset({"M1", "M5"})

_POLICY_TFS = frozenset(tf.value for tf in TIMEFRAME_LADDER)

_TF_SECONDS = {
    "M1": 60,
    "M5": 300,
    "M15": 900,
    "M30": 1800,
    "H1": 3600,
    "H4": 14400,
    "D1": 86400,
    "W1": 604800,
}
_GENERIC_WATCH_REASONS = frozenset(
    {
        "awaiting clearer entry and risk context",
        "setup needs better timing or context before trade",
        "ai-reviewed setup",
        "visual and engine context support trade consideration",
        "review rejects trade under current conditions",
    }
)
_STYLE_EXPIRY_MAX = {
    "scalp": 7200,
    "intraday": 28800,
    "swing": 259200,
}


def _coerce_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        v = float(value)
        if v != v or abs(v) == float("inf"):
            return None
        return v
    if isinstance(value, str) and value.strip():
        try:
            v = float(value.strip())
        except ValueError:
            return None
        if v != v or abs(v) == float("inf"):
            return None
        return v
    return None


def _coerce_int(value: Any) -> int | None:
    f = _coerce_float(value)
    if f is None:
        return None
    return int(f)


def _pick_plan(raw: dict[str, Any]) -> dict[str, Any] | None:
    for key in ("suggestedTradePlan", "suggested_trade_plan"):
        val = raw.get(key)
        if isinstance(val, dict):
            return val
    structured = raw.get("structured")
    if isinstance(structured, dict):
        for key in ("suggestedTradePlan", "suggested_trade_plan"):
            val = structured.get(key)
            if isinstance(val, dict):
                return val
    return None


def sanitize_suggested_trade_plan(
    raw: dict[str, Any] | None,
    *,
    source: str,
    symbol: str,
) -> dict[str, Any] | None:
    """Return a sanitized plan dict or None. Sets armable=false when invalid."""
    if not isinstance(raw, dict):
        return None

    plan_src = _pick_plan(raw)
    if plan_src is None and raw.get("direction") and raw.get("action"):
        plan_src = raw
    if not isinstance(plan_src, dict):
        return None

    direction = str(plan_src.get("direction") or "").upper().strip()
    action = str(plan_src.get("action") or "").upper().strip()
    trigger = str(plan_src.get("triggerType") or plan_src.get("trigger_type") or "").upper().strip()
    sym = str(plan_src.get("symbol") or symbol or "").upper().strip()
    src = str(plan_src.get("source") or source or "").strip()

    level = _coerce_float(plan_src.get("level"))
    zone_low = _coerce_float(plan_src.get("zoneLow") if "zoneLow" in plan_src else plan_src.get("zone_low"))
    zone_high = _coerce_float(plan_src.get("zoneHigh") if "zoneHigh" in plan_src else plan_src.get("zone_high"))
    expires = _coerce_int(plan_src.get("expiresInSeconds") if "expiresInSeconds" in plan_src else plan_src.get("expires_in_seconds"))

    armable = True
    reason_parts: list[str] = []

    if direction not in _VALID_DIRECTIONS:
        armable = False
        reason_parts.append("invalid direction")
    if action not in _VALID_ACTIONS:
        armable = False
        reason_parts.append("invalid action")
    if action != "WATCH_ONLY" and trigger not in _VALID_TRIGGERS:
        armable = False
        reason_parts.append("invalid triggerType")
    if not sym:
        armable = False
        reason_parts.append("missing symbol")
    if src not in _VALID_SOURCES:
        src = source if source in _VALID_SOURCES else "ai_chart_review"

    has_level = level is not None and level > 0
    has_zone = zone_low is not None and zone_high is not None and zone_low > 0 and zone_high > 0 and zone_low <= zone_high
    if trigger in ("ACCEPTANCE_ABOVE", "ACCEPTANCE_BELOW") and not has_level:
        armable = False
        reason_parts.append("level required for acceptance trigger")
    if trigger in ("PULLBACK_TO_ZONE", "REJECTION_FROM_ZONE", "SWEEP_RECLAIM") and not has_zone:
        armable = False
        reason_parts.append("zone required for zone trigger")

    if action in ("ENTRY_NOW", "NO_TRADE"):
        armable = False

    if action == "WATCH_ONLY":
        armable = True

    if action in _VALID_WATCH_ACTIONS and not expires:
        expires = None  # expiry may be filled by monitor default on flag

    out: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "armable": armable,
        "source": src,
        "symbol": sym,
        "direction": direction if direction in _VALID_DIRECTIONS else "LONG",
        "action": action if action in _VALID_ACTIONS else "NO_TRADE",
        "triggerType": trigger if trigger in _VALID_TRIGGERS else (
            "ACCEPTANCE_ABOVE" if action != "WATCH_ONLY" else ""
        ),
    }
    if level is not None:
        out["level"] = level
    if zone_low is not None:
        out["zoneLow"] = zone_low
    if zone_high is not None:
        out["zoneHigh"] = zone_high
    for camel, snake in (
        ("contextTf", "context_tf"),
        ("entryTf", "entry_tf"),
        ("executionTf", "execution_tf"),
    ):
        val = plan_src.get(camel) if camel in plan_src else plan_src.get(snake)
        if val:
            out[camel] = str(val).upper()
    inv_above = _coerce_float(plan_src.get("invalidateAbove") if "invalidateAbove" in plan_src else plan_src.get("invalidate_above"))
    inv_below = _coerce_float(plan_src.get("invalidateBelow") if "invalidateBelow" in plan_src else plan_src.get("invalidate_below"))
    if inv_above is not None:
        out["invalidateAbove"] = inv_above
    if inv_below is not None:
        out["invalidateBelow"] = inv_below

    if src == "ai_scalp_chart_review" and action in _VALID_WATCH_ACTIONS and action != "WATCH_ONLY":
        ctx_tf = str(out.get("contextTf") or "").upper()
        entry_tf = str(out.get("entryTf") or "").upper()
        exec_tf = str(out.get("executionTf") or "").upper()
        if ctx_tf and ctx_tf not in _VALID_SCALP_CONTEXT_TF:
            armable = False
            reason_parts.append("invalid contextTf for scalp")
        if entry_tf and entry_tf not in _VALID_SCALP_ENTRY_EXEC_TF:
            armable = False
            reason_parts.append("invalid entryTf for scalp")
        if exec_tf and exec_tf not in _VALID_SCALP_ENTRY_EXEC_TF:
            armable = False
            reason_parts.append("invalid executionTf for scalp")
        if inv_above is None and inv_below is None:
            armable = False
            reason_parts.append("invalidateAbove or invalidateBelow required for scalp plan")
        out["armable"] = armable

    if expires is not None and expires > 0:
        out["expiresInSeconds"] = expires
    plan_reason = plan_src.get("reason")
    if reason_parts:
        out["reason"] = "; ".join(reason_parts)
    elif plan_reason:
        out["reason"] = str(plan_reason)

    if not armable and action not in _VALID_WATCH_ACTIONS:
        return out

    if not armable:
        return out

    return out


def backfill_suggested_trade_invalidation(
    plan: dict[str, Any] | None,
    signal: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Backfill a missing watch invalidation from stamped signal geometry.

    Only an explicit signal ``invalidation``/``sl`` is accepted, and only when it
    is beyond the watched level or full watched zone in the safe direction.
    Explicit plan anchors are never replaced.
    """
    if not isinstance(plan, dict):
        return plan
    out = dict(plan)
    if (
        _coerce_float(out.get("invalidateAbove")) is not None
        or _coerce_float(out.get("invalidateBelow")) is not None
    ):
        return out

    action = str(out.get("action") or "").upper()
    if action not in _VALID_WATCH_ACTIONS or action == "WATCH_ONLY":
        return out
    if not isinstance(signal, dict):
        return out

    invalidation = None
    for key in ("invalidation", "sl", "stopLoss", "stop_loss"):
        candidate = _coerce_float(signal.get(key))
        if candidate is not None and candidate > 0:
            invalidation = candidate
            break
    if invalidation is None:
        return out

    direction = str(out.get("direction") or "").upper()
    level = _coerce_float(out.get("level"))
    zone_low = _coerce_float(out.get("zoneLow"))
    zone_high = _coerce_float(out.get("zoneHigh"))
    if direction == "LONG":
        reference = zone_low if zone_low is not None else level
        if reference is not None and invalidation < reference:
            out["invalidateBelow"] = invalidation
    elif direction == "SHORT":
        reference = zone_high if zone_high is not None else level
        if reference is not None and invalidation > reference:
            out["invalidateAbove"] = invalidation
    return out


def _clean_reason(value: Any) -> str:
    text = str(value or "").strip()
    if not text or text.lower() in _GENERIC_WATCH_REASONS:
        return ""
    return text


def _wait_for_clause(plan: dict[str, Any]) -> str:
    trigger = str(plan.get("triggerType") or plan.get("trigger_type") or "").upper()
    level = _coerce_float(plan.get("level"))
    zone_low = _coerce_float(plan.get("zoneLow") if "zoneLow" in plan else plan.get("zone_low"))
    zone_high = _coerce_float(plan.get("zoneHigh") if "zoneHigh" in plan else plan.get("zone_high"))
    if trigger == "ACCEPTANCE_ABOVE" and level is not None:
        return f"Wait for acceptance above {level}."
    if trigger == "ACCEPTANCE_BELOW" and level is not None:
        return f"Wait for acceptance below {level}."
    if trigger in {"PULLBACK_TO_ZONE", "REJECTION_FROM_ZONE", "SWEEP_RECLAIM"}:
        if zone_low is not None and zone_high is not None:
            label = {
                "PULLBACK_TO_ZONE": "pullback into",
                "REJECTION_FROM_ZONE": "rejection from",
                "SWEEP_RECLAIM": "sweep-reclaim of",
            }[trigger]
            return f"Wait for {label} zone {zone_low}-{zone_high}."
    return ""


def resolve_watch_reason(
    *,
    plan: dict[str, Any] | None = None,
    summary: dict[str, Any] | None = None,
    review: dict[str, Any] | None = None,
) -> str:
    """Build the text the monitor and Suggested Trades panel should show.

    Prefers the concrete wait condition (level/zone + waitReason) over a
    generic supporting sentence or model self-score.
    """
    plan = plan if isinstance(plan, dict) else {}
    summary = summary if isinstance(summary, dict) else {}
    review = review if isinstance(review, dict) else {}

    wait = _clean_reason(review.get("waitReason") or review.get("wait_reason"))
    final = _clean_reason(
        summary.get("finalReason")
        or summary.get("final_reason")
        or review.get("finalReason")
        or review.get("final_reason")
    )
    plan_reason = _clean_reason(plan.get("reason"))
    chart = _clean_reason(
        review.get("chartReadSummary") or review.get("chart_read_summary")
    )
    required = review.get("requiredConfirmation") or review.get("required_confirmation") or []
    confirm_text = ""
    if isinstance(required, list):
        confirm_text = ", ".join(str(item).strip() for item in required if str(item).strip())

    parts: list[str] = []
    wait_for = _wait_for_clause(plan)
    if wait_for:
        parts.append(wait_for)
    body = wait or final or plan_reason or chart
    if body and body.lower() not in " ".join(parts).lower():
        parts.append(body)
    if confirm_text and confirm_text.lower() not in " ".join(parts).lower():
        parts.append(f"Need: {confirm_text}.")
    return " ".join(parts).strip()[:500]


def allocate_watch_expiry_seconds(
    plan: dict[str, Any] | None,
    *,
    style: str | None = None,
    cfg: dict[str, Any] | None = None,
) -> int:
    """Size the watch window from the plan's trigger/entry TF, not a flat 30 minutes."""
    cfg = cfg if isinstance(cfg, dict) else {}
    try:
        bars = int(cfg.get("EXPIRY_BARS") or 4)
    except (TypeError, ValueError):
        bars = 4
    try:
        min_sec = int(cfg.get("EXPIRY_MIN_SECONDS") or 900)
    except (TypeError, ValueError):
        min_sec = 900
    try:
        max_sec = int(cfg.get("EXPIRY_MAX_SECONDS") or 259200)
    except (TypeError, ValueError):
        max_sec = 259200
    try:
        default = int(cfg.get("DEFAULT_EXPIRY_SECONDS") or 1800)
    except (TypeError, ValueError):
        default = 1800
    bars = max(1, bars)
    min_sec = max(60, min_sec)
    max_sec = max(min_sec, max_sec)

    plan = plan if isinstance(plan, dict) else {}
    tf = str(
        plan.get("entryTf")
        or plan.get("entry_tf")
        or plan.get("contextTf")
        or plan.get("context_tf")
        or plan.get("executionTf")
        or plan.get("execution_tf")
        or ""
    ).upper()
    bucket = _TF_SECONDS.get(tf)
    style_key = str(style or "").strip().lower()
    style_caps = cfg.get("EXPIRY_MAX_SECONDS_BY_STYLE")
    if not isinstance(style_caps, dict):
        style_caps = _STYLE_EXPIRY_MAX
    style_max = style_caps.get(style_key)
    try:
        style_max_sec = int(style_max) if style_max is not None else max_sec
    except (TypeError, ValueError):
        style_max_sec = max_sec
    cap = min(max_sec, max(min_sec, style_max_sec))
    if bucket is None:
        return max(min_sec, min(cap, default))
    return max(min_sec, min(cap, bucket * bars))


def is_watchable_plan(plan: dict[str, Any] | None) -> bool:
    if not isinstance(plan, dict):
        return False
    if not plan.get("armable"):
        return False
    action = str(plan.get("action") or "").upper()
    if action not in _VALID_WATCH_ACTIONS:
        return False
    if action == "WATCH_ONLY":
        return True
    trigger = str(plan.get("triggerType") or "").upper()
    if trigger in ("ACCEPTANCE_ABOVE", "ACCEPTANCE_BELOW"):
        return _coerce_float(plan.get("level")) is not None
    if trigger in ("PULLBACK_TO_ZONE", "REJECTION_FROM_ZONE", "SWEEP_RECLAIM"):
        zl = _coerce_float(plan.get("zoneLow"))
        zh = _coerce_float(plan.get("zoneHigh"))
        return zl is not None and zh is not None
    return False


def plan_playbook_warnings(plan: dict[str, Any] | None) -> list[str]:
    """Deterministic, advisory playbook-consistency warnings for a sanitized plan.

    Never blocks flagging — schema/armable validation is the fail-closed layer.
    These surface AI feedback that contradicts the timeframe_policy.v4 contract
    or basic direction/trigger coherence so the UI can show it.
    """
    if not isinstance(plan, dict):
        return []
    warnings: list[str] = []
    source = str(plan.get("source") or "").strip()
    is_scalp = source in _SCALP_SOURCES

    tf_fields = ("contextTf", "entryTf", "executionTf")
    tfs = {name: str(plan.get(name) or "").upper() for name in tf_fields}
    for name, value in tfs.items():
        if not value:
            continue
        if value not in _POLICY_TFS:
            warnings.append(f"{name} {value} is not a timeframe-policy ladder timeframe")
        elif value == "M1" and not is_scalp:
            warnings.append(f"{name} M1 is scalp/Engine-D-native only under timeframe_policy.v4")

    direction = str(plan.get("direction") or "").upper()
    trigger = str(plan.get("triggerType") or "").upper()
    if direction == "LONG" and trigger == "ACCEPTANCE_BELOW":
        warnings.append("LONG plan waits for acceptance below level — direction/trigger mismatch vs playbook")
    if direction == "SHORT" and trigger == "ACCEPTANCE_ABOVE":
        warnings.append("SHORT plan waits for acceptance above level — direction/trigger mismatch vs playbook")

    action = str(plan.get("action") or "").upper()
    if action in _VALID_WATCH_ACTIONS and action != "WATCH_ONLY":
        has_invalidation = (
            _coerce_float(plan.get("invalidateAbove")) is not None
            or _coerce_float(plan.get("invalidateBelow")) is not None
        )
        if not has_invalidation:
            warnings.append("no invalidateAbove/invalidateBelow — setup has no playbook invalidation anchor")

    return warnings
