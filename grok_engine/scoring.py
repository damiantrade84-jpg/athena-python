"""Deterministic GROK Killzone Displacement Delivery scoring."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import math
from typing import Any

from .config import GrokConfig
from .profiles import resolve_grok_profile
from .indicators import (
    cisd_state,
    clamp,
    dealing_quality,
    dealing_range,
    external_liquidity,
    impulse_vector,
    raid_signature,
    session_pools,
    void_map,
    wilder_atr,
)
from .models import Candle, MarketSnapshot, TIMEFRAME_SECONDS, utc_iso
from .sessions import classify_session, market_is_open


REQUIRED_SIGNAL_GATE_NAMES = frozenset(
    {
        "D1_freshness",
        "H1_freshness",
        "M15_freshness",
        "M5_freshness",
        "session_open",
        "killzone_window",
        "direction_resolved",
        "liquidity_raid_aligned",
        "displacement_aligned",
        "delivery_void_open",
        "delivery_sequence",
        "dealing_range_location",
        "cisd_confirmed",
        "trigger_aligned_recent",
        "execution_geometry",
    }
)


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


def _freshness(snapshot: MarketSnapshot, config: GrokConfig) -> tuple[list[dict[str, Any]], list[str]]:
    gates: list[dict[str, Any]] = []
    failures: list[str] = list(snapshot.quality_errors)
    minimum_bars = config.scan["minimum_bars"]
    maximum_age = config.scan["maximum_closed_bar_age_buckets"]
    for timeframe in ("D1", "H1", "M15", "M5"):
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
        if raw_age < -clock_skew:
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


def _classify_setup(
    clock: dict[str, Any],
    raid: dict[str, Any],
    void: dict[str, Any],
    cisd: dict[str, Any],
    dealing: dict[str, Any],
    direction: int,
) -> str:
    if not raid.get("available"):
        return "NONE"
    kind = str(clock.get("primaryKind") or "")
    raid_kind = str(raid.get("kind") or "")
    if kind == "silver_bullet" and void.get("available") and void.get("open"):
        return "SILVER_BULLET"
    if raid_kind == "SELL_SIDE_RAID" or raid_kind == "BUY_SIDE_RAID":
        if clock.get("primaryWindow") == "asia_range" or raid.get("pool") is not None:
            if cisd.get("confirmed"):
                return "JUDAS_RECLAIM"
    if cisd.get("confirmed") and void.get("available"):
        return "CISD_CONTINUATION"
    directional_ote = dealing.get("longInOte") if direction > 0 else dealing.get("shortInOte")
    if directional_ote and void.get("available"):
        return "OTE_DELIVERY"
    if raid.get("available"):
        return "JUDAS_RECLAIM"
    return "NONE"


def _narrative(raid: dict[str, Any], impulse: dict[str, Any], void: dict[str, Any], cisd: dict[str, Any]) -> str:
    if cisd.get("confirmed") and impulse.get("available") and void.get("inside"):
        return "DELIVER"
    if void.get("available") and void.get("inside") and impulse.get("available"):
        return "RETRACE"
    if impulse.get("available") and raid.get("available"):
        return "DISPLACE"
    if raid.get("available"):
        return "RAID"
    return "RANGE_BUILD"


def _execution_geometry(
    setup_candles: list[Candle],
    trigger_candles: list[Candle],
    raid: dict[str, Any],
    external: dict[str, Any],
    direction: int,
    levels: dict[str, Any],
    atr_period: int,
) -> dict[str, Any]:
    atr = wilder_atr(setup_candles, int(atr_period))
    if not setup_candles or not trigger_candles or atr <= 0 or direction == 0:
        return {"valid": False, "reason": "GEOMETRY_INPUT_UNAVAILABLE", "quality": 0.0, "atr": atr}
    entry = float(trigger_candles[-1].close)
    buffer_distance = atr * float(levels["stop_atr_buffer"])
    event_extreme = float(raid.get("extreme") or entry)
    if direction > 0:
        # Stop at the raid extreme, not min(raid, last-6-bar low). Expanding to
        # an unrelated later wick made liquid metals fail RR after the max-ATR cap.
        stop = event_extreme - buffer_distance
        opposing = external.get("pdh")
    else:
        stop = event_extreme + buffer_distance
        opposing = external.get("pdl")
    risk = abs(entry - stop)
    if entry <= 0 or stop <= 0 or risk <= 0:
        return {
            "valid": False,
            "reason": "INVALID_STOP_GEOMETRY",
            "quality": 0.0,
            "entry": entry,
            "stop": stop,
            "atr": atr,
        }
    stop_atr = risk / atr
    max_stop_atr = float(levels["maximum_stop_atr"])
    if stop_atr > max_stop_atr:
        capped = entry - direction * max_stop_atr * atr
        covers_raid = (direction > 0 and capped <= event_extreme) or (
            direction < 0 and capped >= event_extreme
        )
        on_side = (direction > 0 and capped < entry) or (direction < 0 and capped > entry)
        if covers_raid and on_side:
            stop = capped
            risk = abs(entry - stop)
            stop_atr = max_stop_atr
    target_rr = float(levels["target_rr"])
    target = entry + direction * target_rr * risk
    opposing_buffer = atr * float(levels["opposing_pool_buffer_atr"])
    wall = None
    if isinstance(opposing, (int, float)):
        if direction > 0 and opposing > entry + opposing_buffer:
            wall = float(opposing)
            target = min(target, wall - opposing_buffer)
        elif direction < 0 and opposing < entry - opposing_buffer:
            wall = float(opposing)
            target = max(target, wall + opposing_buffer)
    reward = direction * (target - entry)
    rr = reward / risk if risk > 0 else 0.0
    minimum_rr = float(levels["minimum_rr"])
    valid_side = stop < entry < target if direction > 0 else target < entry < stop
    if stop_atr < float(levels["minimum_stop_atr"]):
        reason = "STOP_TOO_TIGHT"
    elif stop_atr > max_stop_atr + 1e-9:
        reason = "STOP_TOO_WIDE"
    elif not valid_side:
        reason = "LEVELS_WRONG_SIDE"
    elif rr < minimum_rr:
        reason = "OPPOSING_LIQUIDITY_LIMITS_RR"
    else:
        reason = None
    valid = reason is None
    rr_quality = clamp((rr - minimum_rr + 0.40) / max(target_rr - minimum_rr + 0.40, 1e-9))
    stop_quality = clamp(1.0 - abs(stop_atr - 1.05) / 1.70)
    quality = 0.64 * rr_quality + 0.36 * stop_quality if valid else 0.0
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


def _signal_id(display: str, identity_epoch: float, direction: str, setup: str, version: str) -> str:
    raw = f"{version}|{display.upper()}|{int(identity_epoch)}|{direction}|{setup}"
    return "grok_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def _delivery_sequence(
    raid: dict[str, Any],
    impulse: dict[str, Any],
    void: dict[str, Any],
    cisd: dict[str, Any],
) -> dict[str, Any]:
    values = (
        raid.get("eventIndex"),
        impulse.get("startIndex"),
        impulse.get("endIndex"),
        void.get("index"),
        cisd.get("eventIndex"),
    )
    if not all(isinstance(value, int) for value in values):
        return {"passed": False, "reason": "DELIVERY_SEQUENCE_UNAVAILABLE"}
    raid_index, impulse_start, impulse_end, void_index, cisd_index = (int(value) for value in values)
    passed = (
        raid_index <= impulse_start <= impulse_end
        and void_index >= impulse_end
        and cisd_index >= impulse_end
    )
    return {"passed": passed, "reason": None if passed else "DELIVERY_SEQUENCE_INVALID"}


def evaluate_snapshot(
    snapshot: MarketSnapshot,
    config: GrokConfig,
    *,
    generated_at_epoch: float | None = None,
) -> dict[str, Any]:
    generated_at = generated_at_epoch if generated_at_epoch is not None else datetime.now(timezone.utc).timestamp()
    frames = snapshot.frames
    freshness_gates, data_failures = _freshness(snapshot, config)
    profile = resolve_grok_profile(snapshot.pair, config)
    indicators = profile.indicators
    weights = profile.scoring["weights"]
    scoring = profile.scoring
    levels = profile.levels

    d1 = frames.get("D1") or []
    h1 = frames.get("H1") or []
    m15 = frames.get("M15") or []
    m5 = frames.get("M5") or []
    session_source = m15 if str(indicators["session_source_timeframe"]).upper() == "M15" else h1
    atr = wilder_atr(m15, int(indicators["atr_period"]))
    clock = classify_session(snapshot.as_of_epoch, config, session_mode=profile.session_mode)
    session_open = market_is_open(
        snapshot.as_of_epoch,
        config,
        snapshot.asset_type,
        session_mode=profile.session_mode,
    )
    pools = session_pools(session_source, snapshot.as_of_epoch, config)
    external = external_liquidity(d1)
    raid = raid_signature(
        m15,
        pools,
        external,
        atr=atr,
        lookback=int(indicators["raid_lookback_bars"]),
        recent_bars=int(indicators["raid_recent_bars"]),
        min_excursion_atr=float(indicators["raid_min_excursion_atr"]),
    )
    impulse = impulse_vector(
        m15,
        atr=atr,
        min_run=int(indicators["impulse_min_run"]),
        body_fraction=float(indicators["impulse_body_fraction"]),
        range_atr=float(indicators["impulse_range_atr"]),
        single_range_atr=float(indicators["impulse_single_range_atr"]),
        required_direction=int(raid.get("direction") or 0),
    )
    direction_value = int(raid.get("direction") or impulse.get("direction") or 0)
    if impulse.get("available") and raid.get("available") and int(impulse.get("direction") or 0) != int(raid.get("direction") or 0):
        direction_value = int(raid.get("direction") or 0)
    trigger = impulse_vector(
        m5,
        atr=wilder_atr(m5, int(indicators["atr_period"])) or atr,
        min_run=int(indicators["impulse_min_run"]),
        body_fraction=float(indicators["impulse_body_fraction"]),
        range_atr=float(indicators["impulse_range_atr"]),
        single_range_atr=float(indicators["impulse_single_range_atr"]),
        required_direction=direction_value,
    )
    causal_floor_candidates = [
        value
        for value in (raid.get("eventIndex"), impulse.get("endIndex"))
        if isinstance(value, int)
    ]
    void = void_map(
        m15,
        lookback=int(indicators["void_lookback"]),
        atr=atr,
        direction=direction_value,
        minimum_index=max(causal_floor_candidates) if causal_floor_candidates else None,
    )
    dealing = dealing_range(
        h1,
        lookback=int(indicators["dealing_lookback"]),
        ote_inner=float(indicators["ote_inner"]),
        ote_outer=float(indicators["ote_outer"]),
    )
    cisd = cisd_state(m15, raid, impulse, lookback=int(indicators["cisd_lookback"]))

    direction = "LONG" if direction_value > 0 else "SHORT" if direction_value < 0 else "NONE"
    geometry = _execution_geometry(
        m15,
        m5,
        raid,
        external,
        direction_value,
        levels,
        int(indicators["atr_period"]),
    )
    delivery_sequence = _delivery_sequence(raid, impulse, void, cisd)
    setup_kind = _classify_setup(clock, raid, void, cisd, dealing, direction_value)
    narrative = _narrative(raid, impulse, void, cisd)
    location_quality = dealing_quality(dealing, direction_value)

    clock_quality = float(clock.get("quality") or 0.0) if session_open else 0.0
    raid_quality = float(raid.get("strength") or 0.0) if int(raid.get("direction") or 0) == direction_value else 0.0
    impulse_quality = float(impulse.get("strength") or 0.0) if int(impulse.get("direction") or 0) == direction_value else 0.0
    void_quality = float(void.get("strength") or 0.0) if int(void.get("direction") or 0) in {0, direction_value} else 0.0
    if void.get("available") and int(void.get("direction") or 0) not in {0, direction_value}:
        void_quality = 0.0
    cisd_quality = float(cisd.get("strength") or 0.0) if int(cisd.get("direction") or 0) in {0, direction_value} else 0.0

    components = [
        _component(
            "killzone_clock",
            clock_quality,
            float(weights["killzone_clock"]),
            {
                "primaryWindow": clock.get("primaryWindow"),
                "primaryKind": clock.get("primaryKind"),
                "inWindow": clock.get("inWindow"),
                "localIso": clock.get("localIso"),
                "sessionOpen": session_open,
            },
        ),
        _component(
            "liquidity_raid",
            raid_quality,
            float(weights["liquidity_raid"]),
            {
                "kind": raid.get("kind"),
                "pool": _round(raid.get("pool") if isinstance(raid.get("pool"), (int, float)) else None),
                "extreme": _round(raid.get("extreme") if isinstance(raid.get("extreme"), (int, float)) else None),
                "excursionAtr": _round(raid.get("excursionAtr") if isinstance(raid.get("excursionAtr"), (int, float)) else None),
                "eventAgeBars": raid.get("eventAgeBars"),
            },
        ),
        _component(
            "impulse_vector",
            impulse_quality,
            float(weights["impulse_vector"]),
            {
                "spanAtr": _round(impulse.get("spanAtr") if isinstance(impulse.get("spanAtr"), (int, float)) else None),
                "efficiency": _round(impulse.get("efficiency") if isinstance(impulse.get("efficiency"), (int, float)) else None),
                "bars": impulse.get("bars"),
            },
        ),
        _component(
            "void_alignment",
            void_quality,
            float(weights["void_alignment"]),
            {
                "low": _round(void.get("low") if isinstance(void.get("low"), (int, float)) else None),
                "high": _round(void.get("high") if isinstance(void.get("high"), (int, float)) else None),
                "fillFraction": _round(void.get("fillFraction") if isinstance(void.get("fillFraction"), (int, float)) else None),
                "inside": void.get("inside"),
                "open": void.get("open"),
            },
        ),
        _component(
            "dealing_range",
            location_quality,
            float(weights["dealing_range"]),
            {
                "position": _round(dealing.get("position") if isinstance(dealing.get("position"), (int, float)) else None),
                "inOte": dealing.get("inOte"),
                "longInOte": dealing.get("longInOte"),
                "shortInOte": dealing.get("shortInOte"),
                "discount": dealing.get("discount"),
                "premium": dealing.get("premium"),
            },
        ),
        _component(
            "cisd_state",
            cisd_quality,
            float(weights["cisd_state"]),
            {
                "confirmed": cisd.get("confirmed"),
                "forming": cisd.get("forming"),
                "origin": _round(cisd.get("origin") if isinstance(cisd.get("origin"), (int, float)) else None),
            },
        ),
        _component(
            "geometry",
            float(geometry.get("quality") or 0.0),
            float(weights["geometry"]),
            {
                "rr": _round(geometry.get("rr") if isinstance(geometry.get("rr"), (int, float)) else None),
                "stopAtr": _round(geometry.get("stopAtr") if isinstance(geometry.get("stopAtr"), (int, float)) else None),
                "reason": geometry.get("reason"),
            },
        ),
    ]
    score = round(sum(float(item["score"]) for item in components), 2)

    raw_position = dealing.get("position")
    position = float(raw_position) if dealing.get("available") and isinstance(raw_position, (int, float)) else 0.5
    long_location_ok = direction_value <= 0 or position <= float(scoring["maximum_premium_for_long"])
    short_location_ok = direction_value >= 0 or position >= float(scoring["minimum_discount_for_short"])
    trigger_age = trigger.get("ageBars")
    trigger_direction_ok = int(trigger.get("direction") or 0) == direction_value
    trigger_strength_ok = float(trigger.get("strength") or 0.0) >= float(scoring["minimum_impulse_strength"])
    trigger_recent = isinstance(trigger_age, int) and trigger_age <= int(indicators["trigger_recent_bars"])
    trigger_aligned = bool(trigger.get("available")) and trigger_direction_ok and trigger_strength_ok and trigger_recent
    if not trigger.get("available"):
        raw_trigger_reason = str(trigger.get("reason") or "")
        trigger_reason = "TRIGGER_NOT_ALIGNED" if raw_trigger_reason in {"NO_DISPLACEMENT", "NO_ALIGNED_DISPLACEMENT"} else (raw_trigger_reason or "TRIGGER_UNAVAILABLE")
    elif not trigger_direction_ok or not trigger_strength_ok:
        trigger_reason = "TRIGGER_NOT_ALIGNED"
    elif not trigger_recent:
        trigger_reason = "TRIGGER_STALE"
    else:
        trigger_reason = None
    cisd_ok = bool(cisd.get("confirmed")) and int(cisd.get("direction") or 0) == direction_value
    void_ok = bool(void.get("available") and void.get("open") and int(void.get("direction") or 0) == direction_value)
    raid_ok = (
        bool(raid.get("available"))
        and int(raid.get("direction") or 0) == direction_value
        and float(raid.get("strength") or 0.0) >= float(scoring["minimum_raid_strength"])
    )
    impulse_ok = (
        bool(impulse.get("available"))
        and int(impulse.get("direction") or 0) == direction_value
        and float(impulse.get("strength") or 0.0) >= float(scoring["minimum_impulse_strength"])
    )
    clock_ok = clock_quality >= float(scoring["minimum_killzone_quality"]) and session_open

    hard_gates = list(freshness_gates)
    hard_gates.extend(
        [
            {
                "name": "session_open",
                "passed": session_open,
                "reason": None if session_open else "SESSION_CLOSED",
            },
            {
                "name": "killzone_window",
                "passed": clock_ok,
                "reason": None if clock_ok else "OUTSIDE_KILLZONE",
            },
            {
                "name": "direction_resolved",
                "passed": direction_value != 0,
                "reason": None if direction_value != 0 else "DIRECTION_UNRESOLVED",
            },
            {
                "name": "liquidity_raid_aligned",
                "passed": raid_ok,
                "reason": None if raid_ok else (raid.get("reason") or "LIQUIDITY_RAID_NOT_CONFIRMED"),
            },
            {
                "name": "displacement_aligned",
                "passed": impulse_ok,
                "reason": None if impulse_ok else "DISPLACEMENT_NOT_CONFIRMED",
            },
            {
                "name": "delivery_void_open",
                "passed": void_ok,
                "reason": None if void_ok else "DELIVERY_VOID_UNAVAILABLE",
            },
            {
                "name": "delivery_sequence",
                "passed": bool(delivery_sequence["passed"]),
                "reason": delivery_sequence["reason"],
            },
            {
                "name": "dealing_range_location",
                "passed": long_location_ok and short_location_ok,
                "reason": None if long_location_ok and short_location_ok else "DEALING_RANGE_EXTREME",
            },
            {
                "name": "cisd_confirmed",
                "passed": cisd_ok,
                "reason": None if cisd_ok else "CISD_NOT_CONFIRMED",
            },
            {
                "name": "trigger_aligned_recent",
                "passed": trigger_aligned,
                "reason": trigger_reason,
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
    ready_threshold = float(scoring["ready_threshold"])
    watch_threshold = float(scoring["watch_threshold"])
    if hard_pass and score >= ready_threshold:
        decision = "READY"
        decision_reason = "All deterministic GROK gates passed; broker quote attestation is still required."
    elif score >= watch_threshold and not data_failures:
        decision = "WATCH"
        decision_reason = blockers[0] if blockers else f"SCORE_BELOW_READY:{score:.2f}/{ready_threshold:.2f}"
    else:
        decision = "BLOCKED"
        decision_reason = blockers[0] if blockers else f"SCORE_BELOW_WATCH:{score:.2f}/{watch_threshold:.2f}"

    bar_closed_at = m5[-1].closes_at("M5") if m5 else snapshot.as_of_epoch
    setup_event_at = None
    event_index = raid.get("eventIndex")
    if isinstance(event_index, int) and 0 <= event_index < len(m15):
        setup_event_at = m15[event_index].closes_at("M15")
    identity_epoch = setup_event_at if setup_event_at is not None else bar_closed_at
    signal_id = _signal_id(snapshot.display, identity_epoch, direction, setup_kind, config.version)
    return {
        "signalId": signal_id,
        "contractVersion": config.version,
        "pair": snapshot.display,
        "symbol": str(snapshot.pair.get("symbol") or snapshot.display),
        "assetType": snapshot.asset_type,
        "scoreGroup": profile.group,
        "profileFamily": profile.family,
        "grokProfile": profile.public_dict(),
        "venue": snapshot.venue,
        "direction": direction,
        "setup": setup_kind,
        "narrative": narrative,
        "decision": decision,
        "decisionReason": decision_reason,
        "score": score,
        "maxScore": 100.0,
        "readyThreshold": ready_threshold,
        "watchThreshold": watch_threshold,
        "generatedAt": utc_iso(generated_at),
        "barClosedAt": utc_iso(bar_closed_at),
        "setupEventAt": utc_iso(setup_event_at) if setup_event_at is not None else None,
        "timeframes": dict(config.scan["timeframes"]),
        "entry": _round(geometry.get("entry") if isinstance(geometry.get("entry"), (int, float)) else None),
        "stop": _round(geometry.get("stop") if isinstance(geometry.get("stop"), (int, float)) else None),
        "target": _round(geometry.get("target") if isinstance(geometry.get("target"), (int, float)) else None),
        "rr": _round(geometry.get("rr") if isinstance(geometry.get("rr"), (int, float)) else None),
        "atr": _round(geometry.get("atr") if isinstance(geometry.get("atr"), (int, float)) else atr),
        "components": components,
        "gates": hard_gates,
        "blockingReasons": blockers,
        "sessionClock": clock,
        "indicatorState": {
            "sessionPools": pools,
            "externalLiquidity": {key: _round(value) if isinstance(value, float) else value for key, value in external.items()},
            "raid": {key: _round(value) if isinstance(value, float) else value for key, value in raid.items()},
            "impulse": {key: _round(value) if isinstance(value, float) else value for key, value in impulse.items()},
            "triggerImpulse": {key: _round(value) if isinstance(value, float) else value for key, value in trigger.items()},
            "void": {key: _round(value) if isinstance(value, float) else value for key, value in void.items()},
            "dealingRange": {key: _round(value) if isinstance(value, float) else value for key, value in dealing.items()},
            "cisd": {key: _round(value) if isinstance(value, float) else value for key, value in cisd.items()},
        },
        "dataProvenance": snapshot.provenance,
        "execution": {
            "quoteAttested": False,
            "executable": False,
            "mode": str(config.execution["default_mode"]),
        },
    }
