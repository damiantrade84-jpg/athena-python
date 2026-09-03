"""The FABLE narrative scorer.

``evaluate_snapshot`` turns one closed-bar snapshot into a signal payload. The
signal is a five-act story (draw, raid, shift, return, chorus). Each act gets a
quality in [0, 1]; the acts are fused with a weighted geometric mean into a
0-100 *coherence* score. Deterministic gates (data, freshness, ATR sanity,
session window, event blackout, stop and reward geometry) decide whether the
story is tellable at all; coherence decides how good the story is.

Decisions
    EXECUTE  price is inside the imbalance and coherence >= execute threshold
    STAGE    the narrative is complete and price has not yet returned (or the
             coherence is between the stage and execute thresholds)
    OBSERVE  the narrative is incomplete or too weak to act on
    VOID     a deterministic gate failed; nothing about the story can be trusted

Tiers (LEGEND / SAGA / TALE / SKETCH) grade coherence for display; they never
replace the decision.
"""

from __future__ import annotations

import hashlib
import math
from typing import Any, Sequence

from .config import FableConfig
from .models import (
    ACT_NAMES,
    CONTRACT_VERSION,
    ROLE_TIMEFRAMES,
    TIMEFRAME_SECONDS,
    Imbalance,
    LiquidityPool,
    MarketSnapshot,
    Raid,
    Shift,
    utc_iso,
)
from .sessions import session_state
from .structure import (
    atr_series,
    dealing_range,
    dedupe_pools,
    efficiency_ratio,
    find_raids,
    find_shift,
    fractal_swings,
    ny_zone,
    percentile_rank,
    retracement,
    session_extremes,
    swing_pools,
    swing_sequence_bias,
)


# ── small math helpers ──────────────────────────────────────────────────────


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def ramp(value: float, start: float, end: float) -> float:
    """0 at ``start``, 1 at ``end``, linear in between (works for descending ranges)."""
    if start == end:
        return 1.0 if value >= end else 0.0
    return clamp((value - start) / (end - start))


def plateau(value: float, rise_from: float, rise_to: float, fall_from: float, fall_to: float, floor: float = 0.0) -> float:
    """Trapezoid: floor->1 across [rise_from, rise_to], 1 across the plateau, 1->floor across [fall_from, fall_to]."""
    if value < rise_to:
        return floor + (1.0 - floor) * ramp(value, rise_from, rise_to)
    if value <= fall_from:
        return 1.0
    return floor + (1.0 - floor) * (1.0 - ramp(value, fall_from, fall_to))


def signed_toward(value: float | None, direction: str) -> float | None:
    if value is None:
        return None
    return float(value) if direction == "LONG" else -float(value)


def coherence_score(qualities: dict[str, float], weights: dict[str, float], floor: float) -> float:
    """Weighted geometric mean of act qualities, on a 0-100 scale."""
    total_weight = sum(float(weights[name]) for name in ACT_NAMES)
    if total_weight <= 0:
        return 0.0
    log_sum = 0.0
    for name in ACT_NAMES:
        quality = clamp(float(qualities.get(name, 0.0)))
        log_sum += float(weights[name]) * math.log(max(floor, quality))
    return round(100.0 * math.exp(log_sum / total_weight), 3)


def tier_for(coherence: float, tiers: dict[str, Any]) -> str:
    if coherence >= float(tiers["LEGEND"]):
        return "LEGEND"
    if coherence >= float(tiers["SAGA"]):
        return "SAGA"
    if coherence >= float(tiers["TALE"]):
        return "TALE"
    return "SKETCH"


def _gate(name: str, passed: bool, reason: str | None, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"name": name, "passed": bool(passed), "reason": None if passed else reason}
    payload.update(extra)
    return payload


def _act(name: str, quality: float | None, weight: float, *, state: str, evidence: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": name,
        "quality": None if quality is None else round(clamp(quality), 4),
        "weight": float(weight),
        "state": state,
        "evidence": evidence,
    }


def _signal_id(*parts: Any) -> str:
    digest = hashlib.sha1("|".join(str(part) for part in parts).encode("utf-8")).hexdigest()
    return f"fable_{digest[:16]}"


def _round(value: float | None, digits: int = 6) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    return round(float(value), digits)


# ── data / freshness gates ──────────────────────────────────────────────────


def _data_gates(snapshot: MarketSnapshot, config: FableConfig, end_index: int | None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    gates: list[dict[str, Any]] = []
    freshness: dict[str, Any] = {}
    minimum = config.scan["minimum_bars"]
    max_age = config.scan["maximum_closed_bar_age_buckets"]
    for role, timeframe in ROLE_TIMEFRAMES.items():
        series = snapshot.frames.get(timeframe, [])
        if role == "narrative" and end_index is not None:
            series = series[:end_index]
        bars = len(series)
        need = int(minimum[timeframe])
        gates.append(_gate(f"{role}_data", bars >= need, f"INSUFFICIENT_BARS:{timeframe}", bars=bars, minimumBars=need))
        provider = (snapshot.provenance.get(timeframe) or {}).get("provider")
        if bars == 0:
            freshness[timeframe] = {"status": "MISSING", "lastBarIso": None, "ageBuckets": None, "source": provider, "stalenessSeverity": "missing"}
            gates.append(_gate(f"{role}_fresh", False, f"DATA_MISSING:{timeframe}", timeframe=timeframe))
            continue
        last_close = series[-1].closes_at(timeframe)
        age_buckets = (snapshot.as_of_epoch - last_close) / TIMEFRAME_SECONDS[timeframe]
        fresh = age_buckets <= float(max_age[timeframe])
        diag: dict[str, Any] = {
            "status": "FRESH" if fresh else "STALE",
            "lastBarIso": utc_iso(last_close),
            "ageBuckets": round(age_buckets, 3),
            "source": provider,
        }
        if not fresh:
            diag["stalenessSeverity"] = "stale_multi_bucket" if age_buckets > 2.0 else "stale_1_bucket"
        freshness[timeframe] = diag
        gates.append(
            _gate(
                f"{role}_fresh",
                fresh,
                f"DATA_STALE:{timeframe}",
                timeframe=timeframe,
                ageBuckets=round(age_buckets, 3),
                maxAgeBuckets=float(max_age[timeframe]),
                lastClosedAt=utc_iso(last_close),
            )
        )
    for error in snapshot.quality_errors:
        if error.startswith("FUTURE_CANDLES") or error.startswith("SOURCE_ERROR"):
            gates.append(_gate("source_integrity", False, error))
    return gates, freshness


# ── pools ───────────────────────────────────────────────────────────────────


def build_pools(
    snapshot: MarketSnapshot,
    config: FableConfig,
    *,
    atr: float,
    zone,
    as_of_epoch: float,
    m15_end: int | None = None,
) -> list[LiquidityPool]:
    structure = config.structure
    strength = structure["swing_strength"]
    lookback = structure["pool_lookback"]
    pools: list[LiquidityPool] = []
    h4 = snapshot.series("bias")
    h1 = snapshot.series("pools")
    m15 = snapshot.series("narrative")
    if m15_end is not None:
        m15 = m15[:m15_end]
        cutoff = m15[-1].time if m15 else as_of_epoch
        h4 = [candle for candle in h4 if candle.closes_at("H4") <= cutoff + 1]
        h1 = [candle for candle in h1 if candle.closes_at("H1") <= cutoff + 1]
    pools.extend(
        swing_pools(
            h4,
            strength=int(strength["H4"]),
            lookback=int(lookback["H4"]),
            source="H4",
            base_strength=0.70,
            atr=atr,
            equal_tolerance_atr=float(structure["equal_level_tolerance_atr"]),
        )
    )
    pools.extend(
        swing_pools(
            h1,
            strength=int(strength["H1"]),
            lookback=int(lookback["H1"]),
            source="H1",
            base_strength=0.50,
            atr=atr,
            equal_tolerance_atr=float(structure["equal_level_tolerance_atr"]),
        )
    )
    pools.extend(session_extremes(m15, as_of_epoch=as_of_epoch, zone=zone))
    return dedupe_pools(pools, tolerance=atr * float(structure["equal_level_tolerance_atr"]))


# ── act evaluations ─────────────────────────────────────────────────────────


def _draw_act(
    snapshot: MarketSnapshot,
    config: FableConfig,
    *,
    direction: str,
    price: float,
    atr: float,
    pools: Sequence[LiquidityPool],
    weight: float,
    m15_cutoff: float | None,
) -> tuple[dict[str, Any], LiquidityPool | None, tuple[float, float] | None]:
    structure = config.structure
    d1 = snapshot.series("draw")
    h4 = snapshot.series("bias")
    if m15_cutoff is not None:
        d1 = [candle for candle in d1 if candle.closes_at("D1") <= m15_cutoff + 1]
        h4 = [candle for candle in h4 if candle.closes_at("H4") <= m15_cutoff + 1]
    swings_h4 = fractal_swings(h4, int(structure["swing_strength"]["H4"]))
    bias, bias_strength = swing_sequence_bias(swings_h4)
    if bias == direction:
        q_bias = 0.6 + 0.4 * bias_strength
    elif bias == "NONE":
        q_bias = 0.55
    else:
        q_bias = clamp(0.45 - 0.25 * bias_strength, 0.15, 0.45)

    range_pair = dealing_range(d1, int(structure["swing_strength"]["D1"]))
    if range_pair is None:
        position = None
        q_pd = 0.5
    else:
        low, high = range_pair
        position = clamp((price - low) / (high - low), -0.5, 1.5)
        side = position if direction == "LONG" else 1.0 - position
        # Discount for longs / premium for shorts. 0.35 or better -> 1.0; deep in
        # the wrong half fades to 0.15 rather than zero: the raid can be the
        # reversal that resets the range.
        q_pd = clamp(1.0 - (side - 0.35) / 0.5, 0.15, 1.0)

    er = efficiency_ratio(h4, int(structure["efficiency_window"]))
    er_dir = signed_toward(er, direction)
    q_er = 0.55 if er_dir is None else clamp(0.6 + 0.4 * er_dir, 0.2, 1.0)

    # External draw: the nearest pool in the narrative direction with room to run.
    if direction == "LONG":
        candidates = [pool for pool in pools if pool.side == "buyside" and pool.price > price + atr]
    else:
        candidates = [pool for pool in pools if pool.side == "sellside" and pool.price < price - atr]
    candidates.sort(key=lambda pool: abs(pool.price - price))
    target: LiquidityPool | None = candidates[0] if candidates else None
    if target is None:
        q_target = 0.3
        distance_atr = None
    else:
        distance_atr = abs(target.price - price) / atr
        q_target = clamp(target.strength * clamp(distance_atr / 3.0, 0.3, 1.0), 0.2, 1.0)

    quality = 0.35 * q_bias + 0.25 * q_pd + 0.15 * q_er + 0.25 * q_target
    evidence = {
        "biasTf": ROLE_TIMEFRAMES["bias"],
        "bias": bias,
        "biasStrength": round(bias_strength, 3),
        "biasQuality": round(q_bias, 3),
        "dealingRange": None if range_pair is None else {"low": range_pair[0], "high": range_pair[1]},
        "rangePosition": None if position is None else round(position, 3),
        "premiumDiscountQuality": round(q_pd, 3),
        "efficiencyRatio": None if er is None else round(er, 3),
        "efficiencyQuality": round(q_er, 3),
        "drawTarget": None if target is None else target.to_dict(),
        "drawDistanceAtr": None if distance_atr is None else round(distance_atr, 3),
        "drawQuality": round(q_target, 3),
    }
    return _act("draw", quality, weight, state="told", evidence=evidence), target, range_pair


def _raid_act(raid: Raid, config: FableConfig, weight: float) -> dict[str, Any]:
    structure = config.structure
    q_depth = plateau(
        raid.depth_atr,
        float(structure["raid_min_depth_atr"]),
        0.15,
        1.0,
        float(structure["raid_max_depth_atr"]),
        floor=0.3,
    )
    q_reclaim = clamp(raid.reclaim_atr / 0.3, 0.2, 1.0)
    lookback = int(structure["raid_lookback_bars"])
    q_recency = 1.0 if raid.bars_since <= max(4, lookback // 2) else clamp(1.0 - (raid.bars_since - lookback // 2) / max(1, lookback // 2) * 0.7, 0.3, 1.0)
    participation = 0.85 if raid.participation_z is None else 0.85 + 0.15 * clamp(raid.participation_z / 2.0)
    quality = (0.35 * q_depth + 0.2 * q_reclaim + 0.25 * raid.pool.strength + 0.2 * q_recency) * participation
    evidence = {
        **raid.to_dict(),
        "depthQuality": round(q_depth, 3),
        "reclaimQuality": round(q_reclaim, 3),
        "recencyQuality": round(q_recency, 3),
        "participationMultiplier": round(participation, 3),
    }
    return _act("raid", quality, weight, state="told", evidence=evidence)


def _shift_act(shift: Shift, raid: Raid, config: FableConfig, weight: float) -> dict[str, Any]:
    structure = config.structure
    min_disp = float(structure["shift_min_displacement_atr"])
    min_body = float(structure["shift_min_body_atr"])
    q_disp = clamp(0.5 + 0.5 * ramp(shift.displacement_atr, min_disp, min_disp * 2.0))
    q_body = clamp(0.5 + 0.5 * ramp(shift.max_body_atr, min_body, min_body * 2.2))
    has_fvg = any(item.kind == "fvg" for item in shift.imbalances)
    q_imbalance = 1.0 if has_fvg else (0.6 if shift.imbalances else 0.0)
    bars_to_break = shift.break_index - raid.reclaim_index
    q_speed = 1.0 if bars_to_break <= 6 else clamp(1.0 - (bars_to_break - 6) / 18.0 * 0.7, 0.3, 1.0)
    participation = 0.85 if shift.participation_z is None else 0.85 + 0.15 * clamp(shift.participation_z / 2.0)
    quality = (0.35 * q_disp + 0.25 * q_body + 0.2 * q_imbalance + 0.2 * q_speed) * participation
    evidence = {
        **shift.to_dict(),
        "displacementQuality": round(q_disp, 3),
        "bodyQuality": round(q_body, 3),
        "imbalanceQuality": round(q_imbalance, 3),
        "barsToBreak": bars_to_break,
        "speedQuality": round(q_speed, 3),
        "participationMultiplier": round(participation, 3),
    }
    return _act("shift", quality, weight, state="told", evidence=evidence)


def select_array(shift: Shift, config: FableConfig) -> Imbalance | None:
    """The PD array to return into: the FVG nearest the OTE centre, else the order block."""
    ote_center = (float(config.structure["ote_low"]) + float(config.structure["ote_high"])) / 2.0
    gaps = [item for item in shift.imbalances if item.kind == "fvg"]
    if gaps:
        return min(gaps, key=lambda item: abs(retracement(shift, item.mid) - ote_center))
    blocks = [item for item in shift.imbalances if item.kind == "order_block"]
    return blocks[0] if blocks else None


def _return_act(
    shift: Shift,
    array: Imbalance,
    config: FableConfig,
    *,
    price: float,
    atr: float,
    weight: float,
) -> tuple[dict[str, Any], str]:
    structure = config.structure
    tolerance = atr * float(structure["return_tolerance_atr"])
    ote_low = float(structure["ote_low"])
    ote_high = float(structure["ote_high"])
    ote_center = (ote_low + ote_high) / 2.0
    r = retracement(shift, price)
    r_array_near = retracement(shift, array.high if shift.direction == "LONG" else array.low)
    if shift.direction == "LONG":
        inside = array.low - tolerance <= price <= array.high + tolerance
        through = price < array.low - tolerance
    else:
        inside = array.low - tolerance <= price <= array.high + tolerance
        through = price > array.high + tolerance
    if inside:
        state = "inside"
    elif through:
        state = "through"
    else:
        state = "pending"
    position_quality = clamp(1.0 - abs(r - ote_center) / 0.5)
    array_quality = 1.0 if array.kind == "fvg" else 0.8
    depth_quality = clamp((array.high - array.low) / atr / 0.35, 0.4, 1.0)
    quality = position_quality * array_quality * depth_quality if state == "inside" else 0.0
    potential = clamp(1.0 - abs(r_array_near - ote_center) / 0.5) * array_quality * depth_quality
    if shift.direction == "LONG":
        distance_atr = (price - array.high) / atr
    else:
        distance_atr = (array.low - price) / atr
    evidence = {
        "array": array.to_dict(),
        "retracement": round(r, 3),
        "oteLow": ote_low,
        "oteHigh": ote_high,
        "inOte": ote_low <= r <= ote_high,
        "positionQuality": round(position_quality, 3),
        "arrayQuality": array_quality,
        "depthQuality": round(depth_quality, 3),
        "distanceToArrayAtr": round(distance_atr, 3),
        "potentialQuality": round(potential, 3),
        "toleranceAtr": float(structure["return_tolerance_atr"]),
    }
    return _act("return", quality, weight, state=state, evidence=evidence), state


def _chorus_act(
    snapshot: MarketSnapshot,
    config: FableConfig,
    *,
    direction: str,
    session: dict[str, Any],
    atr_pct_rank: float | None,
    participation_z: float | None,
    context: dict[str, Any],
    weight: float,
) -> dict[str, Any]:
    chorus_cfg = config.scoring["chorus"]
    asset_type = snapshot.asset_type
    voices: list[tuple[str, float | None, float, Any]] = []

    session_gated = asset_type in {str(item).lower() for item in config.sessions.get("apply_window_gate_to") or []}
    session_quality = float(session.get("quality") or 0.0)
    q_session = session_quality if session_gated else 0.7 + 0.3 * session_quality
    voices.append(("session", q_session, float(chorus_cfg["session_weight"]), session.get("window")))

    if atr_pct_rank is None:
        voices.append(("volatility", None, float(chorus_cfg["volatility_weight"]), None))
    else:
        q_vol = plateau(atr_pct_rank, 0.05, 0.25, 0.85, 1.0, floor=0.3)
        voices.append(("volatility", q_vol, float(chorus_cfg["volatility_weight"]), round(atr_pct_rank, 3)))

    if participation_z is None:
        voices.append(("participation", None, float(chorus_cfg["participation_weight"]), None))
    else:
        q_part = clamp(0.3 + 0.7 * ramp(participation_z, -0.5, 1.5))
        voices.append(("participation", q_part, float(chorus_cfg["participation_weight"]), round(participation_z, 3)))

    carry = context.get("carryZ") if asset_type == "forex" else None
    carry_dir = signed_toward(carry, direction)
    voices.append(("carry", None if carry_dir is None else 0.5 + 0.5 * clamp(carry_dir / 2.0, -1.0, 1.0), float(chorus_cfg["carry_weight"]), carry))

    cot = context.get("cotZ") if asset_type in {"forex", "commodity", "index"} else None
    cot_dir = signed_toward(cot, direction)
    voices.append(("positioning", None if cot_dir is None else 0.5 + 0.5 * clamp(cot_dir / 2.0, -1.0, 1.0), float(chorus_cfg["cot_weight"]), cot))

    skew = context.get("volSkewZ")
    if skew is None:
        voices.append(("volSkew", None, float(chorus_cfg["vol_skew_weight"]), None))
    else:
        # Elevated skew is a risk-off headwind for longs in risk assets.
        headwind = clamp(float(skew) / 2.0, -1.0, 1.0)
        q_skew = 0.5 - 0.5 * headwind if direction == "LONG" else 0.5 + 0.5 * headwind
        voices.append(("volSkew", clamp(q_skew), float(chorus_cfg["vol_skew_weight"]), skew))

    funding = context.get("fundingRate") if asset_type == "crypto" else None
    if funding is None:
        voices.append(("funding", None, float(chorus_cfg["funding_weight"]), None))
    else:
        crowd = clamp(float(funding) / 0.0005, -1.0, 1.0)  # +0.05%/8h = crowded longs
        q_funding = 0.5 - 0.5 * crowd if direction == "LONG" else 0.5 + 0.5 * crowd
        voices.append(("funding", clamp(q_funding), float(chorus_cfg["funding_weight"]), funding))

    available = [(name, quality, voice_weight, raw) for name, quality, voice_weight, raw in voices if quality is not None]
    total_weight = sum(voice_weight for _, _, voice_weight, _ in available)
    quality = sum(q * w for _, q, w, _ in available) / total_weight if total_weight > 0 else 0.5
    evidence = {
        "voices": [
            {"name": name, "quality": None if q is None else round(q, 3), "weight": w, "raw": raw}
            for name, q, w, raw in voices
        ],
        "sessionGated": session_gated,
        "session": session,
        "eventRisk": context.get("eventRisk"),
    }
    return _act("chorus", quality, weight, state="told", evidence=evidence)


# ── levels ──────────────────────────────────────────────────────────────────


def build_levels(
    *,
    direction: str,
    price: float,
    atr: float,
    raid: Raid,
    shift: Shift,
    pools: Sequence[LiquidityPool],
    draw_target: LiquidityPool | None,
    levels_cfg: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    gates: list[dict[str, Any]] = []
    buffer = atr * float(levels_cfg["stop_buffer_atr"])
    target_buffer = atr * float(levels_cfg["target_liquidity_buffer_atr"])
    min_stop = float(levels_cfg["minimum_stop_atr"])
    max_stop = float(levels_cfg["maximum_stop_atr"])
    minimum_rr = float(levels_cfg["minimum_rr"])
    if direction == "LONG":
        stop = raid.extreme - buffer
    else:
        stop = raid.extreme + buffer
    risk = abs(price - stop)
    stop_atr = risk / atr if atr > 0 else math.inf
    stop_ok = math.isfinite(stop_atr) and min_stop <= stop_atr <= max_stop and risk > 0
    reason = None
    if not stop_ok:
        reason = "STOP_TOO_TIGHT" if stop_atr < min_stop else "STOP_TOO_WIDE"
    gates.append(_gate("stop_geometry", stop_ok, reason, stopAtr=round(stop_atr, 4) if math.isfinite(stop_atr) else None, minimumStopAtr=min_stop, maximumStopAtr=max_stop))

    candidates: list[tuple[str, float]] = []
    if direction == "LONG":
        candidates.append(("leg_high", shift.leg_end))
        for pool in sorted((p for p in pools if p.side == "buyside" and p.price > price), key=lambda p: p.price):
            candidates.append((pool.source, pool.price - target_buffer))
    else:
        candidates.append(("leg_low", shift.leg_end))
        for pool in sorted((p for p in pools if p.side == "sellside" and p.price < price), key=lambda p: -p.price):
            candidates.append((pool.source, pool.price + target_buffer))

    def rr_of(target: float) -> float:
        if risk <= 0:
            return 0.0
        reward = (target - price) if direction == "LONG" else (price - target)
        return reward / risk

    target1: float | None = None
    target1_source: str | None = None
    for source, level in candidates:
        if rr_of(level) >= minimum_rr:
            target1, target1_source = level, source
            break
    target2: float | None = None
    target2_source: str | None = None
    if draw_target is not None:
        level = draw_target.price - target_buffer if direction == "LONG" else draw_target.price + target_buffer
        if target1 is not None and ((direction == "LONG" and level > target1) or (direction == "SHORT" and level < target1)):
            target2, target2_source = level, draw_target.source
    rr1 = rr_of(target1) if target1 is not None else 0.0
    rr2 = rr_of(target2) if target2 is not None else None
    gates.append(
        _gate(
            "reward_geometry",
            target1 is not None and rr1 >= minimum_rr,
            "RR_BELOW_MINIMUM",
            rr=round(rr1, 4),
            minimumRr=minimum_rr,
            candidatesTried=len(candidates),
        )
    )
    levels = {
        "entry": _round(price),
        "stop": _round(stop),
        "target": _round(target1),
        "target2": _round(target2),
        "rr": round(rr1, 4),
        "rr2": None if rr2 is None else round(rr2, 4),
        "stopAtr": round(stop_atr, 4) if math.isfinite(stop_atr) else None,
        "targetSource": target1_source,
        "target2Source": target2_source,
        "atr": _round(atr),
    }
    return levels, gates


# ── narrative prose ─────────────────────────────────────────────────────────


def _fmt(value: float | None, digits: int = 5) -> str:
    if value is None:
        return "—"
    magnitude = abs(value)
    if magnitude >= 1000:
        return f"{value:,.2f}"
    if magnitude >= 10:
        return f"{value:.3f}"
    return f"{value:.{digits}f}"


def compose_narrative(
    *,
    display: str,
    direction: str,
    raid: Raid | None,
    shift: Shift | None,
    array: Imbalance | None,
    return_state: str | None,
    draw_target: LiquidityPool | None,
    decision: str,
) -> str:
    if raid is None:
        return f"{display}: no liquidity pool has been raided inside the lookback — the story has not started."
    side = "sellside" if raid.direction == "LONG" else "buyside"
    sentences = [
        f"{display} raided {side} liquidity at {raid.pool.source} {_fmt(raid.pool.price)} "
        f"({raid.depth_atr:.2f} ATR deep, reclaimed {raid.bars_since} bars ago)."
    ]
    if shift is None:
        sentences.append("Displacement has not yet shifted structure; the raid is unconfirmed.")
        return " ".join(sentences)
    sentences.append(
        f"Price then displaced {shift.displacement_atr:.2f} ATR and closed through {_fmt(shift.broken_level)}, "
        f"shifting structure {'up' if direction == 'LONG' else 'down'}."
    )
    if array is None:
        sentences.append("The leg left no imbalance to return into.")
        return " ".join(sentences)
    label = "fair value gap" if array.kind == "fvg" else "order block"
    if return_state == "inside":
        sentences.append(f"Price is inside the {label} {_fmt(array.low)}–{_fmt(array.high)} in the optimal trade entry band.")
    elif return_state == "pending":
        sentences.append(f"The {label} {_fmt(array.low)}–{_fmt(array.high)} is waiting for price to return.")
    else:
        sentences.append(f"Price has traded through the {label}; the return failed.")
    if draw_target is not None:
        sentences.append(f"The draw on liquidity is {draw_target.source} at {_fmt(draw_target.price)}.")
    sentences.append(f"Decision: {decision}.")
    return " ".join(sentences)


# ── main entry point ────────────────────────────────────────────────────────


def evaluate_snapshot(
    snapshot: MarketSnapshot,
    config: FableConfig,
    *,
    generated_at_epoch: float,
    context: dict[str, Any] | None = None,
    end_index: int | None = None,
) -> dict[str, Any]:
    """Evaluate one snapshot and return the FABLE signal payload.

    ``end_index`` (exclusive) restricts the narrative series to a causal prefix
    for chronicle replay; higher timeframes are cut at the same wall clock.
    """
    context = dict(context or {})
    scoring = config.scoring
    weights = {name: float(scoring["weights"][name]) for name in ACT_NAMES}
    display = snapshot.display
    direction = "NONE"
    zone = ny_zone(str(config.sessions.get("timezone") or "America/New_York"), float(config.sessions.get("fallback_utc_offset_hours") or -4.0))

    m15_full = snapshot.series("narrative")
    m15 = m15_full[:end_index] if end_index is not None else m15_full
    as_of = snapshot.as_of_epoch if end_index is None else (m15[-1].closes_at("M15") if m15 else snapshot.as_of_epoch)
    if end_index is not None:
        snapshot = MarketSnapshot(
            pair=snapshot.pair,
            frames=snapshot.frames,
            provenance=snapshot.provenance,
            as_of_epoch=as_of,
            quality_errors=list(snapshot.quality_errors),
        )
    gates, freshness = _data_gates(snapshot, config, end_index)
    session = session_state(as_of, config)
    generated_at = utc_iso(generated_at_epoch)

    acts: dict[str, dict[str, Any]] = {}
    levels: dict[str, Any] = {"entry": None, "stop": None, "target": None, "target2": None, "rr": None, "rr2": None, "stopAtr": None, "atr": None}
    annotations: dict[str, Any] = {"pools": [], "raid": None, "shift": None, "array": None, "dealingRange": None}
    raid: Raid | None = None
    shift: Shift | None = None
    array: Imbalance | None = None
    draw_target: LiquidityPool | None = None
    return_state: str | None = None
    price: float | None = None
    atr: float | None = None
    atr_pct: float | None = None
    stage_reason: str | None = None

    def finish(decision: str, reason: str) -> dict[str, Any]:
        coherence = coherence_score({name: (acts[name]["quality"] or 0.0) if name in acts else 0.0 for name in ACT_NAMES}, weights, float(scoring["quality_floor"])) if len(acts) == len(ACT_NAMES) else 0.0
        potential_qualities = {name: (acts[name]["quality"] or 0.0) if name in acts else 0.0 for name in ACT_NAMES}
        if "return" in acts:
            potential_qualities["return"] = float(acts["return"]["evidence"].get("potentialQuality") or 0.0)
        potential = coherence_score(potential_qualities, weights, float(scoring["quality_floor"])) if len(acts) == len(ACT_NAMES) else 0.0
        tier = tier_for(coherence, scoring["tiers"]) if decision != "VOID" else "SKETCH"
        failing = [gate["reason"] for gate in gates if not gate["passed"] and gate.get("reason")]
        bar_closed_at = utc_iso(m15[-1].closes_at("M15")) if m15 else None
        if raid is not None and shift is not None:
            signal_id = _signal_id(CONTRACT_VERSION, display, direction, int(m15[raid.reclaim_index].time), int(m15[shift.break_index].time))
        elif raid is not None:
            signal_id = _signal_id(CONTRACT_VERSION, display, direction, int(m15[raid.reclaim_index].time), "raid")
        else:
            signal_id = _signal_id(CONTRACT_VERSION, display, "none", bar_closed_at or generated_at, "void")
        return {
            "signalId": signal_id,
            "contractVersion": CONTRACT_VERSION,
            "engine": "FABLE",
            "pair": display,
            "symbol": snapshot.symbol,
            "assetType": snapshot.asset_type,
            "venue": snapshot.venue,
            "direction": direction,
            "decision": decision,
            "decisionReason": reason,
            "tier": tier,
            "coherence": coherence,
            "coherencePotential": potential,
            "maxCoherence": 100.0,
            "executeThreshold": float(scoring["execute_threshold"]),
            "stageThreshold": float(scoring["stage_threshold"]),
            "acts": [acts[name] for name in ACT_NAMES if name in acts],
            "gates": gates,
            "voidReasons": failing,
            "narrative": compose_narrative(
                display=display,
                direction=direction,
                raid=raid,
                shift=shift,
                array=array,
                return_state=return_state,
                draw_target=draw_target,
                decision=decision,
            ),
            "returnState": return_state,
            "stageReason": stage_reason,
            "session": session,
            "timeframes": dict(ROLE_TIMEFRAMES),
            "generatedAt": generated_at,
            "barClosedAt": bar_closed_at,
            "scanClose": _round(price),
            "atr": levels.get("atr"),
            "atrPct": _round(atr_pct, 6),
            "entry": levels.get("entry"),
            "stop": levels.get("stop"),
            "target": levels.get("target"),
            "target2": levels.get("target2"),
            "rr": levels.get("rr"),
            "rr2": levels.get("rr2"),
            "stopAtr": levels.get("stopAtr"),
            "targetSource": levels.get("targetSource"),
            "target2Source": levels.get("target2Source"),
            "annotations": annotations,
            "dataFreshness": freshness,
            "dataProvenance": snapshot.provenance,
            "chorusContext": {key: value for key, value in context.items() if key != "eventRisk"},
        }

    if not all(gate["passed"] for gate in gates):
        return finish("VOID", "DATA_GATE_FAILED")

    px = float(m15[-1].close)
    price = px
    atr_values = atr_series(m15, int(config.structure["atr_period"]))
    last_atr = atr_values[-1]
    if last_atr is None or last_atr <= 0:
        gates.append(_gate("atr_sanity", False, "ATR_UNAVAILABLE"))
        return finish("VOID", "ATR_UNAVAILABLE")
    atr_v = float(last_atr)
    atr = atr_v
    atr_pct = atr_v / px
    atr_ok = float(scoring["atr_pct_min"]) <= atr_pct <= float(scoring["atr_pct_max"])
    gates.append(_gate("atr_sanity", atr_ok, "ATR_OUT_OF_RANGE", atrPct=round(atr_pct, 6), minimum=float(scoring["atr_pct_min"]), maximum=float(scoring["atr_pct_max"])))
    levels["atr"] = _round(atr_v)
    if not atr_ok:
        return finish("VOID", "ATR_OUT_OF_RANGE")

    event_risk = context.get("eventRisk")
    if isinstance(event_risk, dict) and event_risk.get("allowed") is False:
        gates.append(_gate("event_blackout", False, "EVENT_BLACKOUT", detail=event_risk.get("reason")))
        return finish("VOID", "EVENT_BLACKOUT")
    gates.append(_gate("event_blackout", True, None, detail=(event_risk or {}).get("reason") if isinstance(event_risk, dict) else "no calendar"))

    session_gated = snapshot.asset_type in {str(item).lower() for item in config.sessions.get("apply_window_gate_to") or []}
    session_ok = (not session_gated) or float(session["quality"]) >= float(config.sessions["minimum_window_quality"])
    gates.append(_gate("session_window", session_ok, "SESSION_WINDOW_CLOSED", window=session.get("window"), quality=session.get("quality"), gated=session_gated))
    if not session_ok:
        return finish("VOID", "SESSION_WINDOW_CLOSED")

    pools = build_pools(snapshot, config, atr=atr_v, zone=zone, as_of_epoch=as_of, m15_end=end_index)
    annotations["pools"] = [pool.to_dict() for pool in pools if abs(pool.price - px) <= 12 * atr_v]
    structure = config.structure
    raids = find_raids(
        m15,
        pools,
        atr=atr_v,
        lookback=int(structure["raid_lookback_bars"]),
        max_excursion_bars=int(structure["raid_max_excursion_bars"]),
        min_depth_atr=float(structure["raid_min_depth_atr"]),
        max_depth_atr=float(structure["raid_max_depth_atr"]),
        participation_baseline=int(structure["participation_baseline_window"]),
    )
    chosen_raid: Raid | None = None
    chosen_shift: Shift | None = None
    for candidate in raids[:4]:
        found = find_shift(
            m15,
            candidate,
            atr=atr_v,
            swing_strength=int(structure["swing_strength"]["M15"]),
            min_displacement_atr=float(structure["shift_min_displacement_atr"]),
            min_body_atr=float(structure["shift_min_body_atr"]),
            max_bars_after_raid=int(structure["shift_max_bars_after_raid"]),
            participation_baseline=int(structure["participation_baseline_window"]),
        )
        if found is not None:
            chosen_raid, chosen_shift = candidate, found
            break
    if chosen_raid is None and raids:
        chosen_raid = raids[0]
    raid = chosen_raid
    shift = chosen_shift
    if raid is None:
        return finish("OBSERVE", "NO_RAID")
    direction = raid.direction
    annotations["raid"] = {"time": int(m15[raid.start_index].time), "reclaimTime": int(m15[raid.reclaim_index].time), "price": raid.extreme, "pool": raid.pool.to_dict()}

    atr_history = [value for value in atr_values[-int(structure["atr_percentile_window"]) :] if value is not None]
    atr_rank = percentile_rank(atr_history, atr_v)
    m15_cutoff = m15[-1].closes_at("M15") if end_index is not None else None
    draw_act, draw_target, range_pair = _draw_act(
        snapshot,
        config,
        direction=direction,
        price=px,
        atr=atr_v,
        pools=pools,
        weight=weights["draw"],
        m15_cutoff=m15_cutoff,
    )
    acts["draw"] = draw_act
    annotations["dealingRange"] = None if range_pair is None else {"low": range_pair[0], "high": range_pair[1]}
    acts["raid"] = _raid_act(raid, config, weights["raid"])
    if shift is None:
        acts["shift"] = _act("shift", 0.0, weights["shift"], state="awaiting", evidence={"awaiting": "displacement through the pre-raid swing"})
        acts["return"] = _act("return", 0.0, weights["return"], state="awaiting", evidence={})
        acts["chorus"] = _chorus_act(
            snapshot, config, direction=direction, session=session, atr_pct_rank=atr_rank,
            participation_z=raid.participation_z, context=context, weight=weights["chorus"],
        )
        return finish("OBSERVE", "AWAITING_SHIFT")
    annotations["shift"] = {
        "time": int(m15[shift.break_index].time),
        "brokenLevel": shift.broken_level,
        "brokenTime": int(m15[shift.broken_swing_index].time),
        "legEnd": shift.leg_end,
        "legEndTime": int(m15[shift.leg_end_index].time),
    }
    acts["shift"] = _shift_act(shift, raid, config, weights["shift"])
    array = select_array(shift, config)
    if array is None:
        acts["return"] = _act("return", 0.0, weights["return"], state="absent", evidence={"absent": "no fair value gap or order block in the leg"})
        acts["chorus"] = _chorus_act(
            snapshot, config, direction=direction, session=session, atr_pct_rank=atr_rank,
            participation_z=max(filter(lambda z: z is not None, (raid.participation_z, shift.participation_z)), default=None),
            context=context, weight=weights["chorus"],
        )
        return finish("OBSERVE", "NO_IMBALANCE")
    annotations["array"] = array.to_dict()
    return_act, return_state = _return_act(shift, array, config, price=px, atr=atr_v, weight=weights["return"])
    acts["return"] = return_act
    participation = [z for z in (raid.participation_z, shift.participation_z) if z is not None]
    acts["chorus"] = _chorus_act(
        snapshot, config, direction=direction, session=session, atr_pct_rank=atr_rank,
        participation_z=max(participation) if participation else None, context=context, weight=weights["chorus"],
    )

    if return_state == "through":
        return finish("OBSERVE", "RETURN_FAILED")

    # Inside the imbalance the entry is the market; while the return is still
    # pending the plan is measured from the array edge price would enter at.
    if return_state == "inside":
        planned_entry = px
    else:
        planned_entry = float(array.high if direction == "LONG" else array.low)
    levels_cfg = config.levels_for(snapshot.asset_type)
    computed_levels, level_gates = build_levels(
        direction=direction,
        price=planned_entry,
        atr=atr_v,
        raid=raid,
        shift=shift,
        pools=pools,
        draw_target=draw_target,
        levels_cfg=levels_cfg,
    )
    levels.update(computed_levels)
    gates.extend(level_gates)
    if not all(gate["passed"] for gate in level_gates):
        return finish("VOID", next(gate["reason"] for gate in level_gates if not gate["passed"]))

    execute_threshold = float(scoring["execute_threshold"])
    stage_threshold = float(scoring["stage_threshold"])
    qualities = {name: float(acts[name]["quality"] or 0.0) for name in ACT_NAMES}
    coherence = coherence_score(qualities, weights, float(scoring["quality_floor"]))
    potential_qualities = dict(qualities)
    potential_qualities["return"] = float(acts["return"]["evidence"].get("potentialQuality") or 0.0)
    potential = coherence_score(potential_qualities, weights, float(scoring["quality_floor"]))

    if return_state == "pending":
        if potential >= execute_threshold:
            stage_reason = "AWAITING_RETURN"
            return finish("STAGE", "AWAITING_RETURN")
        return finish("OBSERVE", "POTENTIAL_BELOW_THRESHOLD")
    if coherence >= execute_threshold:
        return finish("EXECUTE", "NARRATIVE_COHERENT")
    if coherence >= stage_threshold:
        stage_reason = "COHERENCE_BELOW_EXECUTE"
        return finish("STAGE", "COHERENCE_BELOW_EXECUTE")
    return finish("OBSERVE", "COHERENCE_BELOW_STAGE")
