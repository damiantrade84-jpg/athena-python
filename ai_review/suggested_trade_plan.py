"""Sanitize advisory-only suggested trade plans from AI chart review responses."""

from __future__ import annotations

from typing import Any

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
_VALID_SCALP_CONTEXT_TF = frozenset({"M5", "M15"})
_VALID_SCALP_ENTRY_EXEC_TF = frozenset({"M1", "M5"})


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
        "triggerType": trigger if trigger in _VALID_TRIGGERS else "ACCEPTANCE_ABOVE",
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
