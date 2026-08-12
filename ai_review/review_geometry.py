"""Review-layer geometry: live RR, zone status, entry quality, SL thesis, WATCH.

Advisory / postprocessor only. Never sizes orders or bypasses risk_check().
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from ai_review.engine_b_structure_contract import (
    evaluate_tp_clears_resistance,
    has_engine_b_structure_evidence,
)


ZoneStatus = Literal["IN_ZONE", "ABOVE_ZONE", "BELOW_ZONE", "UNKNOWN"]
WatchState = Literal["WATCH", "ARMED", "EXPIRED", "INVALIDATED", "PROMOTED", None]


def _f(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if v != v:  # NaN
        return None
    return v


def _clamp(score: float, lo: float = 0.0, hi: float = 100.0) -> int:
    return int(max(lo, min(hi, round(score))))


def compute_rr(
    *,
    entry: float | None,
    sl: float | None,
    tp: float | None,
    direction: str | None,
) -> float | None:
    e, s, t = _f(entry), _f(sl), _f(tp)
    if e is None or s is None or t is None:
        return None
    d = str(direction or "").upper()
    if d == "LONG":
        risk = e - s
        reward = t - e
    elif d == "SHORT":
        risk = s - e
        reward = e - t
    else:
        risk = abs(e - s)
        reward = abs(t - e)
    if risk <= 1e-12:
        return None
    return reward / risk


def compute_rr_bundle(
    *,
    entry: float | None,
    sl: float | None,
    tp1: float | None,
    tp2: float | None,
    direction: str | None,
    live_price: float | None = None,
) -> dict[str, Any]:
    """Signal RR vs live RR; blended assumes half volume at TP2 (MT5 scale-out)."""
    rr1_signal = compute_rr(entry=entry, sl=sl, tp=tp1, direction=direction)
    rr2_signal = compute_rr(entry=entry, sl=sl, tp=tp2, direction=direction)
    blended_signal = None
    if rr1_signal is not None and rr2_signal is not None:
        blended_signal = 0.5 * rr1_signal + 0.5 * rr2_signal
    elif rr1_signal is not None:
        blended_signal = rr1_signal

    price_for_live = _f(live_price) if live_price is not None else None
    rr1_live = compute_rr(entry=price_for_live, sl=sl, tp=tp1, direction=direction)
    rr2_live = compute_rr(entry=price_for_live, sl=sl, tp=tp2, direction=direction)
    blended_live = None
    if rr1_live is not None and rr2_live is not None:
        blended_live = 0.5 * rr1_live + 0.5 * rr2_live
    elif rr1_live is not None:
        blended_live = rr1_live

    # Floor for trend-continuation review geometry (advisory gate for review layer).
    rr_floor = 1.5
    rr_for_gate = blended_live if blended_live is not None else rr1_live
    if rr_for_gate is None:
        rr_for_gate = blended_signal if blended_signal is not None else rr1_signal
    rr_passed_live = (
        rr_for_gate + 1e-12 >= rr_floor if rr_for_gate is not None else None
    )

    return {
        "rr_at_signal_tp1": rr1_signal,
        "rr_at_signal_tp2": rr2_signal,
        "rr_at_signal_blended": blended_signal,
        "rr_live_tp1": rr1_live,
        "rr_live_tp2": rr2_live,
        "rr_live_blended": blended_live,
        "rr_live": rr1_live,
        "rrFloorReview": rr_floor,
        "rrPassedLive": rr_passed_live,
        "live_price": price_for_live,
        "entry_at_signal": _f(entry),
    }


def zone_status(
    live_price: float | None,
    zone_low: float | None,
    zone_high: float | None,
) -> ZoneStatus:
    p, lo, hi = _f(live_price), _f(zone_low), _f(zone_high)
    if p is None or lo is None or hi is None:
        return "UNKNOWN"
    low, high = (lo, hi) if lo <= hi else (hi, lo)
    if low - 1e-12 <= p <= high + 1e-12:
        return "IN_ZONE"
    if p > high:
        return "ABOVE_ZONE"
    return "BELOW_ZONE"


def zone_blocks_execution(status: ZoneStatus, direction: str | None) -> bool:
    d = str(direction or "").upper()
    if status == "ABOVE_ZONE" and d == "LONG":
        return True
    if status == "BELOW_ZONE" and d == "SHORT":
        return True
    return False


def displacement_atr(
    price: float | None,
    ema: float | None,
    atr: float | None,
) -> float | None:
    p, e, a = _f(price), _f(ema), _f(atr)
    if p is None or e is None or a is None or a <= 1e-12:
        return None
    return (p - e) / a


def score_entry_quality(
    *,
    engine_a_ctx: dict[str, Any],
    ai_review: dict[str, Any] | None = None,
) -> int:
    """Deterministic entry quality from geometry — not gate booleans alone."""
    geometry = engine_a_ctx.get("geometry") or {}
    atr = engine_a_ctx.get("atr") or {}
    fd = engine_a_ctx.get("factor_diagnostics") or {}
    components = fd.get("components") or engine_a_ctx.get("component_scores") or {}
    location = components.get("location") if isinstance(components, dict) else {}
    loc_contrib = _f(
        (location or {}).get("contribution")
        if isinstance(location, dict)
        else None
    )

    price = _f(geometry.get("current_price") or geometry.get("live_price"))
    entry = _f(geometry.get("candidate_entry") or geometry.get("entry"))
    ema = _f(
        (engine_a_ctx.get("ema_levels") or {}).get("ema21")
        or (engine_a_ctx.get("ema_levels") or {}).get("ema_trend")
    )
    atr_v = _f(atr.get("atr_value") or atr.get("atr_chart_tf") or atr.get("atrH4"))
    disp = displacement_atr(price if price is not None else entry, ema, atr_v)

    zone = str(engine_a_ctx.get("zone_status") or "UNKNOWN").upper()
    rr_live = _f((engine_a_ctx.get("risk_geometry") or {}).get("rr_live_tp1"))
    if rr_live is None:
        rr_live = _f((engine_a_ctx.get("risk_geometry") or {}).get("rr_live"))

    score = 72.0
    if disp is not None:
        ad = abs(disp)
        if ad > 1.5:
            score -= min(40.0, (ad - 1.5) * 18.0)
        elif ad > 1.0:
            score -= (ad - 1.0) * 12.0
    if loc_contrib is not None:
        if loc_contrib < 0.05:
            score -= 22.0
        elif loc_contrib < 0.10:
            score -= 12.0
        else:
            score += min(8.0, loc_contrib * 20.0)
    if zone in {"ABOVE_ZONE", "BELOW_ZONE"}:
        score -= 18.0
    if rr_live is not None:
        if rr_live < 1.0:
            score -= 25.0
        elif rr_live < 1.5:
            score -= 15.0
        elif rr_live >= 2.0:
            score += 6.0

    # Soft text cues from model (never override geometry floor).
    text = " ".join(
        str(x or "")
        for x in (
            (ai_review or {}).get("entry_quality"),
            (ai_review or {}).get("chartReadSummary"),
        )
    ).lower()
    normalized_entry_quality = str((ai_review or {}).get("entry_quality") or "").strip().lower()
    if normalized_entry_quality in {"poor", "poor_to_fair", "poor-to-fair", "poor to fair"}:
        score = min(score, 45.0)
    elif normalized_entry_quality in {"fair", "fair_to_good", "fair-to-good", "fair to good"}:
        score = min(score, 62.0)
    for marker in ("extended", "chasing", "late entry", "poor location", "out of zone"):
        if marker in text:
            score -= 8.0

    # Gates marked not-required contribute 0 (do not boost score).
    active_gate = fd.get("activeEntryGate") or engine_a_ctx.get("active_entry_gate") or {}
    if isinstance(active_gate, dict) and active_gate.get("required") is False:
        pass  # explicitly zero contribution
    elif isinstance(active_gate, dict) and active_gate.get("passed") is False:
        score -= 15.0

    return _clamp(score)


def check_sl_inside_invalidation_thesis(
    *,
    direction: str | None,
    sl: float | None,
    invalidation_level: float | None,
    required_confirmation: list[Any] | None,
    structural_levels_available: bool,
) -> dict[str, Any]:
    """Flag SL that fires before named structural invalidation (e.g. EMA50)."""
    flags: list[str] = []
    d = str(direction or "").upper()
    sl_f, inv_f = _f(sl), _f(invalidation_level)
    suppress_structural_language = not structural_levels_available

    if sl_f is not None and inv_f is not None and d in {"LONG", "SHORT"}:
        # LONG: thesis dies below inv; SL should be at/below inv, not above it.
        if d == "LONG" and sl_f > inv_f + 1e-9:
            flags.append("SL_INSIDE_INVALIDATION_THESIS")
        if d == "SHORT" and sl_f < inv_f - 1e-9:
            flags.append("SL_INSIDE_INVALIDATION_THESIS")

    # Text scan of required_confirmation for named levels vs SL.
    blob = " ".join(str(x) for x in (required_confirmation or [])).lower()
    if "ema50" in blob and sl_f is not None and inv_f is not None:
        if d == "LONG" and sl_f > inv_f + 1e-9:
            if "SL_INSIDE_INVALIDATION_THESIS" not in flags:
                flags.append("SL_INSIDE_INVALIDATION_THESIS")

    return {
        "blocking_flags": flags,
        "suppress_structural_language": suppress_structural_language,
        "sl": sl_f,
        "invalidation_level": inv_f,
    }


def evaluate_watch_state(
    *,
    engine_a_ctx: dict[str, Any],
    direction_confirmed: bool,
) -> dict[str, Any] | None:
    """Deterministic WATCH arm from Engine A + geometry. AI never gates this."""
    passed = bool(engine_a_ctx.get("passed"))
    direction = str(engine_a_ctx.get("direction") or "").upper()
    if not passed or direction not in {"LONG", "SHORT"} or not direction_confirmed:
        return None

    zone = str(engine_a_ctx.get("zone_status") or "UNKNOWN").upper()
    if zone == "IN_ZONE":
        return None

    fd = engine_a_ctx.get("factor_diagnostics") or {}
    components = fd.get("components") or engine_a_ctx.get("component_scores") or {}
    location = components.get("location") if isinstance(components, dict) else {}
    loc_contrib = _f((location or {}).get("contribution") if isinstance(location, dict) else None)

    geometry = engine_a_ctx.get("geometry") or {}
    atr = engine_a_ctx.get("atr") or {}
    price = _f(geometry.get("current_price") or geometry.get("live_price"))
    ema = _f(
        (engine_a_ctx.get("ema_levels") or {}).get("ema21")
        or (engine_a_ctx.get("ema_levels") or {}).get("ema_trend")
    )
    atr_v = _f(atr.get("atr_value") or atr.get("atr_chart_tf"))
    disp = displacement_atr(price, ema, atr_v)

    loc_poor = loc_contrib is not None and loc_contrib < 0.05
    ext_poor = disp is not None and abs(disp) > 1.5
    if not (loc_poor or ext_poor):
        return None
    if zone == "UNKNOWN" and not ext_poor:
        return None

    # Watch zone: EMA21 ± 0.5 ATR
    watch_low = watch_high = None
    if ema is not None and atr_v is not None and atr_v > 0:
        half = 0.5 * atr_v
        watch_low = ema - half
        watch_high = ema + half

    sl = _f(geometry.get("stop_loss") or geometry.get("sl"))
    tp1 = _f(geometry.get("take_profit") or geometry.get("tp1"))
    tp2 = _f(geometry.get("tp2"))
    # RR at watch midpoint (better price than chase)
    watch_entry = ema
    rr_watch = compute_rr_bundle(
        entry=watch_entry,
        sl=sl,
        tp1=tp1,
        tp2=tp2,
        direction=direction,
        live_price=watch_entry,
    )

    # Min ATR floor on stop distance at re-arm (1.5 ATR)
    min_atr_floor = 1.5
    sl_anchored = sl
    if watch_entry is not None and sl is not None and atr_v is not None and atr_v > 0:
        risk_atr = abs(watch_entry - sl) / atr_v
        if risk_atr + 1e-12 < min_atr_floor:
            if direction == "LONG":
                sl_anchored = watch_entry - min_atr_floor * atr_v
            else:
                sl_anchored = watch_entry + min_atr_floor * atr_v
            rr_watch = compute_rr_bundle(
                entry=watch_entry,
                sl=sl_anchored,
                tp1=tp1,
                tp2=tp2,
                direction=direction,
                live_price=watch_entry,
            )

    artifact = engine_a_ctx.get("validation_artifact") or {}
    artifact_status = str(
        (artifact.get("status") if isinstance(artifact, dict) else None)
        or engine_a_ctx.get("validation_status")
        or ""
    ).upper()
    shadow_only = artifact_status in {"DEMO_UNVALIDATED", "UNVALIDATED"}

    return {
        "state": "WATCH",
        "direction": direction,
        "watch_zone": {"low": watch_low, "high": watch_high, "center": ema},
        "invalidation": _f((engine_a_ctx.get("ema_levels") or {}).get("ema50")),
        "rr_at_watch": rr_watch,
        "sl_anchored": sl_anchored,
        "min_atr_floor": min_atr_floor,
        "shadow_only": shadow_only,
        "execution_permitted": False,
        "reason": (
            f"direction valid but entry poor: zone={zone} "
            f"loc_contrib={loc_contrib} displacement_atr={disp}"
        ),
        "logged_at": datetime.now(timezone.utc).isoformat(),
        "trigger": "deterministic_engine_a_geometry",
        "ai_gated": False,
    }


def attach_review_geometry(
    engine_a_ctx: dict[str, Any],
    *,
    ai_review: dict[str, Any] | None = None,
    direction_confirmed: bool = True,
) -> dict[str, Any]:
    """Mutate-safe: returns enriched copy fields for engine context."""
    ctx = engine_a_ctx
    geometry = dict(ctx.get("geometry") or {})
    direction = str(ctx.get("direction") or "").upper()
    live = _f(geometry.get("current_price") or geometry.get("live_price"))
    entry = _f(geometry.get("candidate_entry") or geometry.get("entry"))
    sl = _f(geometry.get("stop_loss") or geometry.get("sl"))
    tp1 = _f(geometry.get("take_profit") or geometry.get("tp1") or geometry.get("tp"))
    tp2 = _f(geometry.get("tp2"))

    zone = ctx.get("entry_zone") or geometry.get("entry_zone") or {}
    z_lo = z_hi = None
    if isinstance(zone, (list, tuple)) and len(zone) >= 2:
        z_lo, z_hi = _f(zone[0]), _f(zone[1])
    elif isinstance(zone, dict):
        z_lo = _f(zone.get("low") or zone.get("lower"))
        z_hi = _f(zone.get("high") or zone.get("upper"))

    z_status = zone_status(live, z_lo, z_hi)
    rr = compute_rr_bundle(
        entry=entry,
        sl=sl,
        tp1=tp1,
        tp2=tp2,
        direction=direction,
        live_price=live,
    )
    risk_geometry = dict(ctx.get("risk_geometry") or ctx.get("riskGeometry") or {})
    risk_geometry.update(rr)
    # Live gate overrides signal-time rrPassed for review layer.
    if rr.get("rrPassedLive") is not None:
        risk_geometry["rrPassed"] = rr["rrPassedLive"]
        risk_geometry["rr_at_signal"] = rr.get("rr_at_signal_tp1")
        risk_geometry["rr"] = rr.get("rr_live_tp1")

    struct = ctx.get("structure_context") or {}
    structural_ok = has_engine_b_structure_evidence(struct)

    # P1-6: a TP whose path crosses mapped structure without clearing it fails
    # the gate and downgrades riskGeometry.
    tp_gate = struct.get("tp_clears_resistance") if isinstance(struct, dict) else None
    if not isinstance(tp_gate, dict):
        tp_gate = evaluate_tp_clears_resistance(
            direction=direction,
            entry=entry,
            targets={"tp1": tp1, "tp2": tp2},
            structure=struct,
        )
    risk_geometry["tpClearsResistance"] = tp_gate
    if tp_gate.get("downgradeRiskGeometry"):
        risk_geometry["riskGeometryDowngraded"] = True
        risk_geometry["riskGeometryDowngradeReason"] = (
            "tp_clears_resistance failed — target path runs through mapped structure"
        )
    inv = _f(
        (ai_review or {}).get("invalidationLevel")
        or geometry.get("invalidation")
        or (ctx.get("ema_levels") or {}).get("ema50")
    )
    sl_check = check_sl_inside_invalidation_thesis(
        direction=direction,
        sl=sl,
        invalidation_level=inv,
        required_confirmation=(ai_review or {}).get("requiredConfirmation")
        or (ai_review or {}).get("required_confirmation"),
        structural_levels_available=structural_ok,
    )

    out = {
        "zone_status": z_status,
        "zoneStatus": z_status,
        "execution_permitted": not zone_blocks_execution(z_status, direction)
        and "SL_INSIDE_INVALIDATION_THESIS" not in sl_check["blocking_flags"],
        "risk_geometry": risk_geometry,
        "sl_thesis_check": sl_check,
    }
    if zone_blocks_execution(z_status, direction):
        out["execution_block_reason"] = f"zone_status_{z_status.lower()}"
    if "SL_INSIDE_INVALIDATION_THESIS" in sl_check["blocking_flags"]:
        out["execution_permitted"] = False
        out["execution_block_reason"] = "SL_INSIDE_INVALIDATION_THESIS"

    # Temporary merge for entry score
    merged = dict(ctx)
    merged["zone_status"] = z_status
    merged["risk_geometry"] = risk_geometry
    out["entry_quality_score"] = score_entry_quality(
        engine_a_ctx=merged, ai_review=ai_review
    )

    watch = evaluate_watch_state(
        engine_a_ctx={**merged, **out},
        direction_confirmed=direction_confirmed,
    )
    if watch:
        out["watch_state"] = watch
        # Shadow artifacts never promote
        if watch.get("shadow_only"):
            out["execution_permitted"] = False
    return out
