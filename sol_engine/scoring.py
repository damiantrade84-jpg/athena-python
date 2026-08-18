"""Deterministic SOL scoring and readiness classification."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import math
from typing import Any

from .config import SolConfig
from .indicators import (
    clamp,
    displacement_pulse,
    liquidity_event,
    participation_impulse,
    robust_orbit,
    wilder_atr,
)
from .models import Candle, MarketSnapshot, TIMEFRAME_SECONDS, utc_iso


def _round(value: float | None, digits: int = 6) -> float | None:
    if value is None or not math.isfinite(float(value)):
        return None
    return round(float(value), digits)


def _component(name: str, value: float, maximum: float, evidence: dict[str, Any]) -> dict[str, Any]:
    score = clamp(value) * maximum
    return {
        "name": name,
        "score": round(score, 2),
        "maxScore": round(maximum, 2),
        "quality": round(clamp(value), 4),
        "evidence": evidence,
    }


def _freshness(snapshot: MarketSnapshot, config: SolConfig) -> tuple[list[dict[str, Any]], list[str]]:
    gates: list[dict[str, Any]] = []
    failures: list[str] = list(snapshot.quality_errors)
    minimum_bars = config.scan["minimum_bars"]
    maximum_age = config.scan["maximum_closed_bar_age_buckets"]
    for timeframe in ("H4", "H1", "M15", "M5"):
        candles = snapshot.frames.get(timeframe) or []
        enough = len(candles) >= int(minimum_bars[timeframe])
        reason = None if enough else f"INSUFFICIENT_DATA:{timeframe}:{len(candles)}/{minimum_bars[timeframe]}"
        gates.append({"name": f"{timeframe}_minimum_bars", "passed": enough, "reason": reason})
        if reason:
            failures.append(reason)
        if not candles:
            gates.append({"name": f"{timeframe}_freshness", "passed": False, "reason": f"NO_DATA:{timeframe}"})
            failures.append(f"NO_DATA:{timeframe}")
            continue
        close_epoch = candles[-1].closes_at(timeframe)
        raw_age = snapshot.as_of_epoch - close_epoch
        clock_skew = float(config.scan["maximum_clock_skew_sec"])
        future_dated = raw_age < -clock_skew
        if future_dated:
            reason = f"FUTURE_DATA:{timeframe}:{abs(raw_age):.1f}_seconds"
            gates.append(
                {
                    "name": f"{timeframe}_freshness",
                    "passed": False,
                    "reason": reason,
                    "lastClosedAt": utc_iso(close_epoch),
                    "ageSec": round(raw_age, 1),
                    "ageBuckets": round(raw_age / TIMEFRAME_SECONDS[timeframe], 3),
                }
            )
            failures.append(reason)
            continue
        age = max(0.0, raw_age)
        age_buckets = age / TIMEFRAME_SECONDS[timeframe]
        fresh = age_buckets <= float(maximum_age[timeframe]) + 1e-9
        reason = None if fresh else f"STALE_DATA:{timeframe}:{age_buckets:.2f}_buckets"
        gates.append(
            {
                "name": f"{timeframe}_freshness",
                "passed": fresh,
                "reason": reason,
                "lastClosedAt": utc_iso(close_epoch),
                "ageSec": round(age, 1),
                "ageBuckets": round(age_buckets, 3),
            }
        )
        if reason:
            failures.append(reason)
    return gates, failures


def _value_quality(orbit: dict[str, Any], direction: int, maximum_extension: float) -> tuple[float, bool]:
    if not orbit.get("available") or direction == 0:
        return 0.0, False
    z = float(orbit.get("z") or 0.0)
    target = -0.45 if direction > 0 else 0.45
    quality = clamp(1.0 - abs(z - target) / 2.60)
    extension = z * direction
    location_ok = extension <= maximum_extension
    return quality, location_ok


def _execution_geometry(
    setup_candles: list[Candle],
    trigger_candles: list[Candle],
    event: dict[str, Any],
    direction: int,
    config: SolConfig,
) -> dict[str, Any]:
    levels = config.levels
    atr = float(event.get("atr") or wilder_atr(setup_candles, int(config.indicators["atr_period"])))
    if not setup_candles or not trigger_candles or atr <= 0 or direction == 0:
        return {"valid": False, "reason": "GEOMETRY_INPUT_UNAVAILABLE", "quality": 0.0}
    entry = float(trigger_candles[-1].close)
    buffer_distance = atr * float(levels["stop_atr_buffer"])
    recent_setup = setup_candles[-8:]
    event_extreme = float(event.get("extreme") or entry)
    if direction > 0:
        stop = min(event_extreme, min(row.low for row in recent_setup)) - buffer_distance
    else:
        stop = max(event_extreme, max(row.high for row in recent_setup)) + buffer_distance
    risk = abs(entry - stop)
    if entry <= 0 or stop <= 0 or risk <= 0:
        return {"valid": False, "reason": "INVALID_STOP_GEOMETRY", "quality": 0.0}
    stop_atr = risk / atr
    if stop_atr < float(levels["minimum_stop_atr"]):
        return {"valid": False, "reason": "STOP_TOO_TIGHT", "quality": 0.0, "stopAtr": stop_atr}
    if stop_atr > float(levels["maximum_stop_atr"]):
        return {"valid": False, "reason": "STOP_TOO_WIDE", "quality": 0.0, "stopAtr": stop_atr}

    target_rr = float(levels["target_rr"])
    target = entry + direction * target_rr * risk
    opposing_buffer = atr * float(levels["opposing_liquidity_buffer_atr"])
    historical = setup_candles[-40:-3] if len(setup_candles) >= 43 else setup_candles[:-3]
    wall = None
    if historical:
        if direction > 0:
            walls = [row.high for row in historical if row.high > entry + opposing_buffer]
            wall = min(walls) if walls else None
            if wall is not None:
                target = min(target, wall - opposing_buffer)
        else:
            walls = [row.low for row in historical if row.low < entry - opposing_buffer]
            wall = max(walls) if walls else None
            if wall is not None:
                target = max(target, wall + opposing_buffer)
    reward = direction * (target - entry)
    rr = reward / risk if risk > 0 else 0.0
    minimum_rr = float(levels["minimum_rr"])
    valid_side = stop < entry < target if direction > 0 else target < entry < stop
    valid = valid_side and rr >= minimum_rr
    if not valid_side:
        reason = "LEVELS_WRONG_SIDE"
    elif rr < minimum_rr:
        reason = "OPPOSING_LIQUIDITY_LIMITS_RR"
    else:
        reason = None
    rr_quality = clamp((rr - minimum_rr + 0.45) / max(target_rr - minimum_rr + 0.45, 1e-9))
    stop_quality = clamp(1.0 - abs(stop_atr - 0.95) / 1.55)
    quality = 0.62 * rr_quality + 0.38 * stop_quality if valid else 0.0
    return {
        "valid": valid,
        "reason": reason,
        "quality": quality,
        "entry": entry,
        "stop": stop,
        "target": target,
        "risk": risk,
        "reward": reward,
        "rr": rr,
        "atr": atr,
        "stopAtr": stop_atr,
        "opposingLiquidity": wall,
    }


def _signal_id(display: str, bar_closed_at: float, direction: str, setup: str, version: str) -> str:
    raw = f"{version}|{display.upper()}|{int(bar_closed_at)}|{direction}|{setup}"
    return "sol_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def evaluate_snapshot(
    snapshot: MarketSnapshot,
    config: SolConfig,
    *,
    generated_at_epoch: float | None = None,
) -> dict[str, Any]:
    """Evaluate one point-in-time snapshot without mutating its candle series."""

    generated_at = generated_at_epoch if generated_at_epoch is not None else datetime.now(timezone.utc).timestamp()
    frames = snapshot.frames
    freshness_gates, data_failures = _freshness(snapshot, config)
    indicators = config.indicators
    weights = config.scoring["weights"]

    context = robust_orbit(
        frames.get("H4") or [],
        lookback=int(indicators["orbit_lookback"]),
        slope_window=int(indicators["orbit_slope_window"]),
        atr_period=int(indicators["atr_period"]),
    )
    value = robust_orbit(
        frames.get("H1") or [],
        lookback=int(indicators["orbit_lookback"]),
        slope_window=int(indicators["orbit_slope_window"]),
        atr_period=int(indicators["atr_period"]),
    )
    setup = liquidity_event(
        frames.get("M15") or [],
        atr_period=int(indicators["atr_period"]),
        lookback=int(indicators["liquidity_lookback"]),
        recent_bars=int(indicators["liquidity_recent_bars"]),
        excursion_atr_min=float(indicators["sweep_excursion_atr_min"]),
        compression_short_window=int(indicators["compression_short_window"]),
        compression_baseline_window=int(indicators["compression_baseline_window"]),
        compression_ratio_max=float(indicators["compression_ratio_max"]),
    )
    trigger = displacement_pulse(
        frames.get("M5") or [],
        window=int(indicators["trigger_window"]),
        break_lookback=int(indicators["trigger_break_lookback"]),
    )
    participation = participation_impulse(
        frames.get("M5") or [],
        recent_window=int(indicators["participation_recent_window"]),
        baseline_window=int(indicators["participation_baseline_window"]),
        scale_floor=float(indicators["participation_scale_floor"]),
    )

    context_direction = int(context.get("direction") or 0)
    setup_direction = int(setup.get("direction") or 0)
    trigger_direction = int(trigger.get("direction") or 0)
    direction_value = context_direction
    direction = "LONG" if direction_value > 0 else "SHORT" if direction_value < 0 else "NONE"
    value_quality, location_ok = _value_quality(
        value,
        direction_value,
        float(config.scoring["maximum_value_extension_z"]),
    )
    value_conflict = (
        bool(value.get("available"))
        and int(value.get("direction") or 0) == -direction_value
        and float(value.get("quality") or 0.0) >= 0.68
    )
    geometry = _execution_geometry(
        frames.get("M15") or [],
        frames.get("M5") or [],
        setup,
        direction_value,
        config,
    )

    components = [
        _component(
            "regime_orbit",
            float(context.get("quality") or 0.0) if context_direction else 0.0,
            float(weights["regime_orbit"]),
            {
                "slopeAtr": _round(context.get("slopeAtr")),
                "pathEfficiency": _round(context.get("efficiency")),
                "orbitZ": _round(context.get("z")),
            },
        ),
        _component(
            "value_location",
            value_quality,
            float(weights["value_location"]),
            {
                "orbitZ": _round(value.get("z")),
                "slopeAtr": _round(value.get("slopeAtr")),
                "counterDirectionConflict": value_conflict,
            },
        ),
        _component(
            "liquidity_event",
            float(setup.get("strength") or 0.0),
            float(weights["liquidity_event"]),
            {
                "kind": setup.get("kind"),
                "eventAgeBars": setup.get("eventAgeBars"),
                "boundary": _round(setup.get("boundary")),
                "extreme": _round(setup.get("extreme")),
                "compressionRatio": _round(setup.get("compressionRatio")),
                "excursionAtr": _round(setup.get("excursionAtr")),
            },
        ),
        _component(
            "displacement",
            float(trigger.get("strength") or 0.0),
            float(weights["displacement"]),
            {
                "efficiency": _round(trigger.get("efficiency")),
                "bodyFraction": _round(trigger.get("bodyFraction")),
                "microBreak": trigger.get("microBreak"),
            },
        ),
        _component(
            "participation",
            float(participation.get("strength") or 0.0),
            float(weights["participation"]),
            {
                "available": bool(participation.get("available")),
                "robustZ": _round(participation.get("z")),
                "sources": participation.get("sources") or [],
                "reason": participation.get("reason"),
            },
        ),
        _component(
            "execution_geometry",
            float(geometry.get("quality") or 0.0),
            float(weights["execution_geometry"]),
            {
                "rr": _round(geometry.get("rr")),
                "stopAtr": _round(geometry.get("stopAtr")),
                "opposingLiquidity": _round(geometry.get("opposingLiquidity")),
                "reason": geometry.get("reason"),
            },
        ),
    ]
    score = round(sum(float(item["score"]) for item in components), 2)

    hard_gates = list(freshness_gates)
    hard_gates.extend(
        [
            {
                "name": "context_direction",
                "passed": context_direction != 0,
                "reason": None if context_direction else "CONTEXT_DIRECTION_UNRESOLVED",
            },
            {
                "name": "value_not_extended",
                "passed": location_ok,
                "reason": None if location_ok else "VALUE_LOCATION_OVEREXTENDED",
            },
            {
                "name": "value_no_strong_conflict",
                "passed": not value_conflict,
                "reason": "VALUE_LAYER_CONFLICT" if value_conflict else None,
            },
            {
                "name": "liquidity_event_aligned",
                "passed": setup_direction != 0 and setup_direction == direction_value,
                "reason": None if setup_direction != 0 and setup_direction == direction_value else "LIQUIDITY_EVENT_NOT_ALIGNED",
            },
            {
                "name": "trigger_displacement_aligned",
                "passed": (
                    trigger_direction == direction_value
                    and float(trigger.get("strength") or 0.0) >= float(config.scoring["minimum_trigger_strength"])
                ),
                "reason": None
                if (
                    trigger_direction == direction_value
                    and float(trigger.get("strength") or 0.0) >= float(config.scoring["minimum_trigger_strength"])
                )
                else "TRIGGER_DISPLACEMENT_NOT_CONFIRMED",
            },
            {
                "name": "execution_geometry",
                "passed": bool(geometry.get("valid")),
                "reason": geometry.get("reason"),
            },
        ]
    )
    blockers = list(data_failures)
    blockers.extend(str(gate["reason"]) for gate in hard_gates if not gate.get("passed") and gate.get("reason"))
    blockers = list(dict.fromkeys(blockers))
    hard_pass = not blockers
    ready_threshold = float(config.scoring["ready_threshold"])
    watch_threshold = float(config.scoring["watch_threshold"])
    if hard_pass and score >= ready_threshold:
        decision = "READY"
        decision_reason = "All deterministic gates passed; broker quote attestation is still required."
    elif score >= watch_threshold and not data_failures:
        decision = "WATCH"
        decision_reason = blockers[0] if blockers else f"SCORE_BELOW_READY:{score:.2f}/{ready_threshold:.2f}"
    else:
        decision = "BLOCKED"
        decision_reason = blockers[0] if blockers else f"SCORE_BELOW_WATCH:{score:.2f}/{watch_threshold:.2f}"

    m5 = frames.get("M5") or []
    bar_closed_at = m5[-1].closes_at("M5") if m5 else snapshot.as_of_epoch
    setup_kind = str(setup.get("kind") or "NONE")
    setup_event_at = None
    event_index = setup.get("eventIndex")
    setup_candles = frames.get("M15") or []
    if isinstance(event_index, int) and 0 <= event_index < len(setup_candles):
        setup_event_at = setup_candles[event_index].closes_at("M15")
    # One setup event has one executable identity even if the M5 trigger stays
    # aligned across multiple refreshes. BLOCKED/NONE states remain bar-scoped.
    identity_epoch = setup_event_at if setup_event_at is not None else bar_closed_at
    signal_id = _signal_id(snapshot.display, identity_epoch, direction, setup_kind, config.version)
    timeframes = config.scan["timeframes"]
    return {
        "signalId": signal_id,
        "contractVersion": config.version,
        "pair": snapshot.display,
        "symbol": str(snapshot.pair.get("symbol") or snapshot.display),
        "assetType": snapshot.asset_type,
        "venue": snapshot.venue,
        "direction": direction,
        "setup": setup_kind,
        "decision": decision,
        "decisionReason": decision_reason,
        "score": score,
        "maxScore": 100.0,
        "readyThreshold": ready_threshold,
        "watchThreshold": watch_threshold,
        "generatedAt": utc_iso(generated_at),
        "barClosedAt": utc_iso(bar_closed_at),
        "setupEventAt": utc_iso(setup_event_at) if setup_event_at is not None else None,
        "timeframes": dict(timeframes),
        "entry": _round(geometry.get("entry")),
        "stop": _round(geometry.get("stop")),
        "target": _round(geometry.get("target")),
        "rr": _round(geometry.get("rr")),
        "atr": _round(geometry.get("atr")),
        "components": components,
        "gates": hard_gates,
        "blockingReasons": blockers,
        "indicatorState": {
            "contextOrbit": {key: _round(value) if isinstance(value, float) else value for key, value in context.items()},
            "valueOrbit": {key: _round(value) if isinstance(value, float) else value for key, value in value.items()},
            "liquidityEvent": {key: _round(value) if isinstance(value, float) else value for key, value in setup.items()},
            "displacementPulse": {key: _round(value) if isinstance(value, float) else value for key, value in trigger.items()},
            "participationImpulse": {key: _round(value) if isinstance(value, float) else value for key, value in participation.items()},
        },
        "dataProvenance": snapshot.provenance,
        "execution": {
            "quoteAttested": False,
            "executable": False,
            "mode": str(config.execution["default_mode"]),
        },
    }
