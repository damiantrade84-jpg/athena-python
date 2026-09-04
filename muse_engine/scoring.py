"""Deterministic MUSE scoring: harmonic conviction × tide timing × halo nudge."""

from __future__ import annotations

import hashlib
import math
from typing import Any

from .config import MuseConfig
from .models import CONTRACT_VERSION, MarketSnapshot, TIMEFRAME_SECONDS, utc_iso
from .prisms import (
    clamp,
    compass_rose,
    halo_field,
    harmonic_mean,
    haven_lattice,
    spark_confirm,
    surge_arc,
    undertow_echo,
    wilder_atr,
)
from .sessions import market_is_closed, tide_state


REQUIRED_GATE_NAMES = frozenset({
    "D1_freshness", "H4_freshness", "M15_freshness", "M5_freshness",
    "weekend_open", "tide_window", "direction_resolved", "compass_aligned",
    "echo_present", "surge_arc_open", "haven_fresh", "spark_recent",
    "halo_consensus", "levels_viable",
})

SETUP_BY_PHASE = {
    "TIDAL_SLING": "RELEASE",
    "UNDERTOW_RECLAIM": "SETTLE",
    "ARC_CONTINUATION": "SURGE",
    "HAVEN_TAP": "SETTLE",
}


def _gate(name: str, passed: bool, reason: str | None, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"name": name, "passed": bool(passed), "reason": None if passed else reason}
    payload.update(extra)
    return payload


def _freshness(snapshot: MarketSnapshot, config: MuseConfig) -> tuple[list[dict[str, Any]], list[str]]:
    gates: list[dict[str, Any]] = []
    failures: list[str] = list(snapshot.quality_errors)
    minimum_bars = config.scan["minimum_bars"]
    maximum_age = config.scan["maximum_closed_bar_age_buckets"]
    for timeframe in ("D1", "H4", "M15", "M5"):
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
        skew = float(config.scan["maximum_clock_skew_sec"])
        if raw_age < -skew:
            reason = f"FUTURE_DATA:{timeframe}:{abs(raw_age):.1f}s"
            gates.append({"name": f"{timeframe}_freshness", "passed": False, "reason": reason})
            failures.append(reason)
            continue
        age = max(0.0, raw_age)
        buckets = age / TIMEFRAME_SECONDS[timeframe]
        fresh = buckets <= float(maximum_age[timeframe]) + 1e-9
        reason = None if fresh else f"STALE_DATA:{timeframe}:{buckets:.2f}b"
        gates.append({"name": f"{timeframe}_freshness", "passed": fresh, "reason": reason,
                      "lastClosedAt": utc_iso(close_epoch), "ageBuckets": round(buckets, 3)})
        if reason:
            failures.append(reason)
    return gates, failures


def _classify_setup(echo: dict[str, Any], surge: dict[str, Any], haven: dict[str, Any],
                    spark: dict[str, Any], compass: dict[str, Any]) -> tuple[str, str]:
    direction = str(echo.get("direction") or "NONE")
    if direction == "NONE":
        return "NONE", "DRIFT"
    if haven.get("available") and spark.get("confirmed") and surge.get("available"):
        # Nearest haven within reach + fresh spark = release tap.
        return "HAVEN_TAP", "RELEASE"
    if surge.get("available") and spark.get("confirmed"):
        return "TIDAL_SLING", "RELEASE"
    if surge.get("available"):
        return "ARC_CONTINUATION", "SURGE"
    if echo.get("available"):
        return "UNDERTOW_RECLAIM", "SETTLE"
    return "NONE", "PULL"


def build_levels(direction: str, *, echo: dict[str, Any], surge: dict[str, Any],
                 haven: dict[str, Any], atr: float, last_close: float,
                 levels_cfg: dict[str, Any], prisms_cfg: dict[str, Any]) -> dict[str, Any]:
    if direction == "NONE" or atr <= 0:
        return {"viable": False, "reason": "NO_DIRECTION", "entry": None, "stop": None, "target": None}
    leg_start = float(surge.get("legStart", echo.get("base", last_close)))
    leg_end = float(surge.get("legEnd", last_close))
    leg_range = abs(leg_end - leg_start)
    if leg_range <= 0:
        return {"viable": False, "reason": "NO_LEG_RANGE", "entry": None, "stop": None, "target": None}
    inner = float(levels_cfg["entry_ote_inner"])
    outer = float(levels_cfg["entry_ote_outer"])
    sign = 1.0 if direction == "LONG" else -1.0
    # OTE retrace of the surge leg, measured from the extreme.
    entry = leg_end - sign * leg_range * ((inner + outer) / 2.0)
    extreme = float(echo.get("extreme", leg_start))
    buffer = float(levels_cfg["stop_buffer_atr"]) * atr
    stop = extreme - sign * buffer if direction == "LONG" else extreme + buffer
    risk = abs(entry - stop)
    min_stop = float(levels_cfg["minimum_stop_atr"]) * atr
    max_stop = float(levels_cfg["maximum_stop_atr"]) * atr
    if risk < min_stop or risk > max_stop or risk <= 0:
        return {"viable": False, "reason": "STOP_GEOMETRY", "entry": entry, "stop": stop,
                "target": None, "riskAtr": round(risk / atr, 4) if atr else None}
    target_rr = float(levels_cfg["target_rr"])
    target = entry + sign * risk * target_rr
    # Haven gravity: pull the target toward the nearest haven mid when aligned.
    nearest = haven.get("nearest") if isinstance(haven.get("nearest"), dict) else None
    if nearest:
        mid = float(nearest["mid"])
        if (direction == "LONG" and entry < mid) or (direction == "SHORT" and entry > mid):
            haven_edge = mid - sign * float(levels_cfg["haven_buffer_atr"]) * atr
            rr_haven = abs(haven_edge - entry) / risk
            if rr_haven >= float(levels_cfg["minimum_rr"]):
                target = haven_edge
    rr = abs(target - entry) / risk if risk > 0 else 0.0
    if rr < float(levels_cfg["minimum_rr"]):
        return {"viable": False, "reason": "RR_TOO_SMALL", "entry": entry, "stop": stop,
                "target": target, "rr": round(rr, 3)}
    return {"viable": True, "reason": None, "entry": entry, "stop": stop, "target": target,
            "rr": round(rr, 3), "riskAtr": round(risk / atr, 4)}


def evaluate_snapshot(snapshot: MarketSnapshot, config: MuseConfig,
                      context: dict[str, Any] | None = None) -> dict[str, Any]:
    context = dict(context or {})
    gates: list[dict[str, Any]] = []
    blockers: list[str] = []

    freshness_gates, failures = _freshness(snapshot, config)
    gates.extend(freshness_gates)
    blockers.extend(failures)

    closed, weekend_reason = market_is_closed(snapshot.as_of_epoch, config, snapshot.asset_type)
    gates.append(_gate("weekend_open", not closed, weekend_reason))
    if closed:
        blockers.append(weekend_reason or "WEEKEND_CLOSED")

    tide = tide_state(snapshot.as_of_epoch, config)
    tide_quality = clamp(float(tide.get("quality") or 0.0))
    min_tide = float(config.scoring["minimum_tide_quality"])
    # Slack/off-tide never hard-blocks by itself; it scales timing unless deeply dead.
    tide_ok = tide_quality >= min_tide or str(tide.get("kind")) != "slack"
    gates.append(_gate("tide_window", tide_ok, None if tide_ok else f"DEAD_TIDE:{tide_quality:.2f}",
                       tide=tide))
    if not tide_ok:
        blockers.append(f"DEAD_TIDE:{tide_quality:.2f}")

    vector = snapshot.frames.get("M15") or []
    spark_series = snapshot.frames.get("M5") or []
    current = snapshot.frames.get("H4") or []
    prisms_cfg = config.prisms
    atr = wilder_atr(vector, int(prisms_cfg["atr_period"])) if vector else 0.0

    echo = undertow_echo(vector, atr, lookback=int(prisms_cfg["echo_lookback_bars"]),
                         reclaim_bars=int(prisms_cfg["echo_reclaim_bars"]),
                         min_depth_atr=float(prisms_cfg["echo_min_depth_atr"]),
                         max_reclaim_bars=int(prisms_cfg["echo_max_reclaim_bars"]))
    direction = str(echo.get("direction") or "NONE")
    gates.append(_gate("direction_resolved", direction in ("LONG", "SHORT"),
                       None if direction in ("LONG", "SHORT") else "NO_DIRECTION", echo=echo))
    if direction not in ("LONG", "SHORT"):
        blockers.append("NO_DIRECTION")

    compass = compass_rose(current, wilder_atr(current, int(prisms_cfg["atr_period"])) if current else 0.0,
                           channel=int(prisms_cfg["compass_channel"]),
                           slope_span=int(prisms_cfg["compass_slope_span"]),
                           min_slope_atr=float(prisms_cfg["compass_min_slope_atr"]))
    compass_ok = bool(compass.get("available")) and compass.get("direction") in (direction, "NONE")
    gates.append(_gate("compass_aligned", compass_ok,
                       None if compass_ok else "COMPASS_CONFLICT", compass=compass))
    if not compass_ok:
        blockers.append("COMPASS_CONFLICT")

    echo_ok = bool(echo.get("available")) and float(echo.get("quality") or 0.0) >= float(config.scoring["minimum_echo"])
    gates.append(_gate("echo_present", echo_ok, None if echo_ok else "ECHO_WEAK", echo=echo))
    if not echo_ok:
        blockers.append("ECHO_WEAK")

    # Displacement is measured post-echo: the arc starts on the bar after the
    # echo extreme, since the echo bar itself points the wrong way by definition.
    surge = surge_arc(vector, atr, start_index=int(echo.get("index", -1)) + 1 if echo.get("available") else -1,
                      direction=direction, min_run=int(prisms_cfg["surge_min_run"]),
                      body_share=float(prisms_cfg["surge_body_share"]),
                      leg_atr=float(prisms_cfg["surge_leg_atr"]),
                      single_leg_atr=float(prisms_cfg["surge_single_leg_atr"]))
    surge_ok = bool(surge.get("available")) and float(surge.get("quality") or 0.0) >= float(config.scoring["minimum_surge"])
    gates.append(_gate("surge_arc_open", surge_ok, None if surge_ok else "SURGE_WEAK", surge=surge))
    if not surge_ok:
        blockers.append("SURGE_WEAK")

    haven = haven_lattice(vector, lookback=int(prisms_cfg["haven_lookback"]),
                          max_age_bars=int(prisms_cfg["haven_max_age_bars"]),
                          fresh_boost=float(prisms_cfg["haven_fresh_boost"]))
    haven_ok = bool(haven.get("available")) and float(haven.get("quality") or 0.0) >= float(config.scoring["minimum_haven"])
    gates.append(_gate("haven_fresh", haven_ok, None if haven_ok else "HAVEN_STALE", haven=haven))
    if not haven_ok:
        blockers.append("HAVEN_STALE")

    spark = spark_confirm(spark_series, wilder_atr(spark_series, int(prisms_cfg["atr_period"])) if spark_series else 0.0,
                          direction=direction, recent_bars=int(prisms_cfg["spark_recent_bars"]),
                          min_body_atr=float(prisms_cfg["spark_min_body_atr"]))
    spark_ok = bool(spark.get("confirmed"))
    gates.append(_gate("spark_recent", spark_ok, None if spark_ok else "SPARK_STALE", spark=spark))
    if not spark_ok:
        blockers.append("SPARK_STALE")

    # Halo advisory: median voice; strong dissent blocks PRIME but not STAGE.
    trend_hint = 1.0 if direction == "LONG" else (-1.0 if direction == "SHORT" else 0.0)
    voices = dict(context)
    voices.setdefault("trendZ", trend_hint * clamp(float(compass.get("quality") or 0.0)) * 2.0)
    halo = halo_field(voices, direction)
    halo_dissent = float(halo.get("dissent") or 0.0)
    halo_ok = not halo.get("veto") and halo_dissent <= float(config.scoring["maximum_halo_dissent"])
    gates.append(_gate("halo_consensus", halo_ok, halo.get("reason") if not halo_ok else None, halo=halo))
    if halo.get("veto"):
        blockers.append(str(halo.get("reason") or "EVENT_RISK_VETO"))
    elif not halo_ok:
        blockers.append("HALO_DISSENT")

    last_close = vector[-1].close if vector else 0.0
    levels = build_levels(direction, echo=echo, surge=surge, haven=haven, atr=atr,
                          last_close=last_close, levels_cfg=config.levels, prisms_cfg=prisms_cfg)
    gates.append(_gate("levels_viable", bool(levels.get("viable")), levels.get("reason"), levels=levels))
    if not levels.get("viable"):
        blockers.append(str(levels.get("reason") or "LEVELS_INVALID"))

    # Fusion: harmonic conviction × tide timing × halo nudge (all distinct math).
    floor = float(config.scoring["harmonic_floor"])
    conviction = harmonic_mean([float(echo.get("quality") or 0.0), float(surge.get("quality") or 0.0),
                                float(haven.get("quality") or 0.0), float(compass.get("quality") or 0.0)], floor)
    timing = float(config.scoring["timing_base"]) + float(config.scoring["timing_gain"]) * tide_quality
    halo_modifier = clamp(float(halo.get("modifier") or 1.0),
                          float(config.scoring["halo_floor"]), float(config.scoring["halo_ceiling"]))
    score = round(100.0 * clamp(conviction) * clamp(timing, 0.0, 1.0) * halo_modifier, 2)
    score = min(100.0, max(0.0, score))

    setup, phase = _classify_setup(echo, surge, haven, spark, compass)
    prime_threshold = float(config.scoring["prime_threshold"])
    stage_threshold = float(config.scoring["stage_threshold"])
    if blockers:
        # SPARK_STALE alone downgrades to STAGE when conviction is sufficient.
        if blockers == ["SPARK_STALE"] and score >= stage_threshold and setup != "NONE":
            decision, reason = "STAGE", "AWAITING_SPARK"
            blockers = []
        else:
            decision, reason = "BLOCKED", blockers[0]
    elif score >= prime_threshold and setup in ("TIDAL_SLING", "HAVEN_TAP"):
        decision, reason = "PRIME", "Harmonic conviction with fresh spark and haven lattice."
    elif score >= stage_threshold and setup != "NONE":
        decision, reason = "STAGE", f"SCORE_BELOW_PRIME:{score:.2f}/{prime_threshold:.2f}"
    elif setup != "NONE":
        decision, reason = "DORMANT", f"SCORE_BELOW_STAGE:{score:.2f}/{stage_threshold:.2f}"
    else:
        decision, reason = "DORMANT", "NO_SETUP"

    prisms = [
        {"name": "echo", "quality": round(clamp(float(echo.get("quality") or 0.0)), 4), "evidence": echo},
        {"name": "surge", "quality": round(clamp(float(surge.get("quality") or 0.0)), 4), "evidence": surge},
        {"name": "haven", "quality": round(clamp(float(haven.get("quality") or 0.0)), 4), "evidence": haven},
        {"name": "compass", "quality": round(clamp(float(compass.get("quality") or 0.0)), 4), "evidence": compass},
    ]
    signal_id = "muse_" + hashlib.sha256(
        f"{snapshot.symbol}|{snapshot.as_of_epoch:.0f}|{direction}|{score:.2f}".encode()).hexdigest()[:16]
    return {
        "signalId": signal_id,
        "contractVersion": CONTRACT_VERSION,
        "engine": "MUSE",
        "pair": snapshot.display,
        "symbol": snapshot.symbol,
        "assetType": snapshot.asset_type,
        "venue": snapshot.venue,
        "direction": direction,
        "setup": setup,
        "phase": phase,
        "decision": decision,
        "decisionReason": reason,
        "score": score,
        "maxScore": 100.0,
        "primeThreshold": prime_threshold,
        "stageThreshold": stage_threshold,
        "conviction": round(conviction, 4),
        "timingFactor": round(timing, 4),
        "haloModifier": round(halo_modifier, 4),
        "tide": tide,
        "halo": halo,
        "spark": spark,
        "entry": levels.get("entry"),
        "stop": levels.get("stop"),
        "target": levels.get("target"),
        "rr": levels.get("rr"),
        "atr": round(atr, 6) if atr else None,
        "prisms": prisms,
        "gates": gates,
        "blockingReasons": list(blockers),
        "generatedAt": utc_iso(snapshot.as_of_epoch),
        "barClosedAt": utc_iso(vector[-1].time) if vector else utc_iso(snapshot.as_of_epoch),
        "timeframes": {"atlas": "D1", "current": "H4", "vector": "M15", "spark": "M5"},
        "museExecution": True,
        "source": "muse_engine",
        "dataProvenance": snapshot.provenance,
    }
