from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from statistics import fmean
from typing import Any, Iterable

from engine_a_v3.contract import PredicateResult
from engine_a_v3.routing import SpecialistRoute


@dataclass(frozen=True)
class SetupCandidate:
    setup_id: str | None
    decision: str
    direction: str | None
    predicates: tuple[PredicateResult, ...]
    rejection_reasons: tuple[str, ...]


def _ema(values: Iterable[float], period: int) -> list[float]:
    rows = [float(value) for value in values]
    if not rows:
        return []
    alpha = 2.0 / (period + 1.0)
    out = [rows[0]]
    for value in rows[1:]:
        out.append(alpha * value + (1.0 - alpha) * out[-1])
    return out


def _atr(candles: list[dict], period: int = 14) -> float:
    if len(candles) < 2:
        return 0.0
    trs = []
    for previous, current in zip(candles[:-1], candles[1:]):
        high = float(current["high"])
        low = float(current["low"])
        prev_close = float(previous["close"])
        trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    return fmean(trs[-period:]) if trs else 0.0


def _predicate(name: str, passed: bool, actual, expected: str) -> PredicateResult:
    return PredicateResult(name=name, passed=bool(passed), actual=actual, expected=expected)


def _parse_time(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return (
        parsed.replace(tzinfo=timezone.utc)
        if parsed.tzinfo is None
        else parsed.astimezone(timezone.utc)
    )


def _session_window(route: SpecialistRoute) -> tuple[int, int, str]:
    if route.family == "forex":
        return 6, 21, "London/NY 06:00-21:00 UTC"
    if route.subclass in {"xau", "precious"}:
        return 6, 21, "London/NY 06:00-21:00 UTC"
    if route.subclass in {"us_indices", "us_stock_single", "bond_tlt", "smallcap_em_etf"}:
        return 13, 21, "US cash session 13:00-21:00 UTC"
    if route.subclass == "eu_indices":
        return 7, 17, "European cash session 07:00-17:00 UTC"
    if route.subclass == "asian_indices":
        return 0, 9, "Asian cash session 00:00-09:00 UTC"
    if route.subclass == "jse_equity":
        return 7, 16, "JSE cash session 07:00-16:00 UTC"
    return 6, 18, "regional cash session 06:00-18:00 UTC"


def _with_session_gate(
    candidate: SetupCandidate,
    primary: list[dict],
    route: SpecialistRoute,
) -> SetupCandidate:
    start_hour, end_hour, label = _session_window(route)
    current_time = _parse_time(primary[-1].get("time") or primary[-1].get("datetime"))
    active = current_time is not None and start_hour <= current_time.hour < end_hour
    predicates = candidate.predicates + (
        _predicate(
            "active_session_utc",
            active,
            current_time.isoformat() if current_time else None,
            label,
        ),
    )
    if active:
        return SetupCandidate(
            candidate.setup_id,
            candidate.decision,
            candidate.direction,
            predicates,
            candidate.rejection_reasons,
        )
    return SetupCandidate(
        candidate.setup_id,
        "NO_SIGNAL",
        candidate.direction,
        predicates,
        tuple(dict.fromkeys(candidate.rejection_reasons + ("outside_active_session_utc",))),
    )


def _trend_direction(context: list[dict]) -> tuple[str | None, tuple[PredicateResult, ...]]:
    closes = [float(candle["close"]) for candle in context]
    if len(closes) < 60:
        return None, (
            _predicate("context_history", False, len(closes), "at least 60 bars"),
        )
    ema20 = _ema(closes, 20)
    ema50 = _ema(closes, 50)
    long_ok = ema20[-1] > ema50[-1] and closes[-1] > ema20[-1]
    short_ok = ema20[-1] < ema50[-1] and closes[-1] < ema20[-1]
    direction = "LONG" if long_ok else "SHORT" if short_ok else None
    return direction, (
        _predicate("context_trend_long", long_ok, round(ema20[-1] - ema50[-1], 8), "> 0"),
        _predicate("context_trend_short", short_ok, round(ema20[-1] - ema50[-1], 8), "< 0"),
    )


def _pullback_candidate(
    setup_id: str,
    primary: list[dict],
    context: list[dict],
    *,
    touch_distance_atr: float = 0.35,
    max_extension_atr: float = 1.25,
) -> SetupCandidate:
    direction, context_predicates = _trend_direction(context)
    closes = [float(candle["close"]) for candle in primary]
    ema20 = _ema(closes, 20)
    atr = _atr(primary)
    if direction is None or len(primary) < 60 or atr <= 0:
        reasons = ["trend_context_not_directional"] if direction is None else ["insufficient_primary_history"]
        return SetupCandidate(setup_id, "NO_SIGNAL", direction, context_predicates, tuple(reasons))

    previous = primary[-2]
    current = primary[-1]
    ema_now = ema20[-1]
    touched = (
        float(previous["low"]) <= ema_now + touch_distance_atr * atr
        if direction == "LONG"
        else float(previous["high"]) >= ema_now - touch_distance_atr * atr
    )
    confirmed = (
        float(current["close"]) > ema_now and float(current["close"]) > float(current["open"])
        if direction == "LONG"
        else float(current["close"]) < ema_now and float(current["close"]) < float(current["open"])
    )
    extension = abs(float(current["close"]) - ema_now) <= max_extension_atr * atr
    predicates = context_predicates + (
        _predicate(
            "pullback_touched_ema20",
            touched,
            previous["low"] if direction == "LONG" else previous["high"],
            f"touch EMA20 within {touch_distance_atr:.2f} ATR",
        ),
        _predicate("confirmation_close", confirmed, current["close"], f"{direction} confirmation"),
        _predicate(
            "entry_not_extended",
            extension,
            round(abs(float(current["close"]) - ema_now) / atr, 4),
            f"<= {max_extension_atr:.2f} ATR",
        ),
    )
    if touched and confirmed and extension:
        return SetupCandidate(setup_id, "TRADE", direction, predicates, ())
    if touched and extension:
        return SetupCandidate(
            setup_id,
            "WATCH",
            direction,
            predicates,
            ("confirmation_close_missing",),
        )
    reasons = tuple(predicate.name for predicate in predicates if not predicate.passed)
    return SetupCandidate(setup_id, "NO_SIGNAL", direction, predicates, reasons)


def _breakout_retest_candidate(
    setup_id: str,
    primary: list[dict],
    context: list[dict],
    *,
    require_contraction: bool,
    lookback: int = 20,
) -> SetupCandidate:
    direction, context_predicates = _trend_direction(context)
    if direction is None or len(primary) < 60:
        return SetupCandidate(
            setup_id,
            "NO_SIGNAL",
            direction,
            context_predicates,
            ("trend_context_not_directional",),
        )
    prior = primary[-(lookback + 2):-2]
    breakout = primary[-2]
    retest = primary[-1]
    high_level = max(float(candle["high"]) for candle in prior)
    low_level = min(float(candle["low"]) for candle in prior)
    level = high_level if direction == "LONG" else low_level
    broke = (
        float(breakout["close"]) > high_level
        if direction == "LONG"
        else float(breakout["close"]) < low_level
    )
    retested = (
        float(retest["low"]) <= level <= float(retest["close"])
        if direction == "LONG"
        else float(retest["high"]) >= level >= float(retest["close"])
    )
    accepted = (
        float(retest["close"]) > float(retest["open"])
        if direction == "LONG"
        else float(retest["close"]) < float(retest["open"])
    )
    recent_ranges = [float(candle["high"]) - float(candle["low"]) for candle in primary[-12:-2]]
    baseline_ranges = [float(candle["high"]) - float(candle["low"]) for candle in primary[-42:-12]]
    contraction = (
        fmean(recent_ranges) < 0.8 * fmean(baseline_ranges)
        if recent_ranges and baseline_ranges and fmean(baseline_ranges) > 0
        else False
    )
    predicates = context_predicates + (
        _predicate(
            "breakout_close",
            broke,
            breakout["close"],
            f"{lookback}-bar close beyond {level}",
        ),
        _predicate("level_retest", retested, retest["close"], f"retest {level}"),
        _predicate("retest_acceptance", accepted, retest["close"], f"{direction} close"),
    )
    if require_contraction:
        predicates += (
            _predicate("volatility_contraction", contraction, contraction, "recent range < 80% baseline"),
        )
    required = broke and retested and accepted and (contraction or not require_contraction)
    if required:
        return SetupCandidate(setup_id, "TRADE", direction, predicates, ())
    if broke and (contraction or not require_contraction):
        return SetupCandidate(setup_id, "WATCH", direction, predicates, ("retest_not_confirmed",))
    reasons = tuple(predicate.name for predicate in predicates if not predicate.passed)
    return SetupCandidate(setup_id, "NO_SIGNAL", direction, predicates, reasons)


def _opening_range_gap_candidate(
    setup_id: str,
    primary: list[dict],
    context: list[dict],
    route: SpecialistRoute,
) -> SetupCandidate:
    direction, context_predicates = _trend_direction(context)
    start_hour, end_hour, label = _session_window(route)
    current_time = _parse_time(primary[-1].get("time") or primary[-1].get("datetime"))
    session_rows: list[tuple[datetime, dict]] = []
    if current_time is not None:
        for candle in primary:
            candle_time = _parse_time(candle.get("time") or candle.get("datetime"))
            if (
                candle_time is not None
                and candle_time.date() == current_time.date()
                and start_hour <= candle_time.hour < end_hour
            ):
                session_rows.append((candle_time, candle))
    has_history = direction is not None and len(primary) >= 60 and len(session_rows) >= 2
    predicates = context_predicates + (
        _predicate(
            "opening_range_history",
            has_history,
            len(session_rows),
            f"at least 2 confirmed H1 bars in {label}",
        ),
    )
    if not has_history or current_time is None:
        reasons = ["opening_range_history"] if direction is not None else ["trend_context_not_directional"]
        return SetupCandidate(setup_id, "NO_SIGNAL", direction, predicates, tuple(reasons))

    opening_rows = session_rows[:2]
    opening_start = opening_rows[0][0]
    prior_rows = [
        candle
        for candle in primary
        if (_parse_time(candle.get("time") or candle.get("datetime")) or opening_start)
        < opening_start
    ]
    if not prior_rows:
        return SetupCandidate(
            setup_id,
            "NO_SIGNAL",
            direction,
            predicates
            + (_predicate("prior_session_close", False, None, "confirmed close before cash open"),),
            ("prior_session_close_missing",),
        )

    atr = _atr(primary)
    opening_high = max(float(candle["high"]) for _, candle in opening_rows)
    opening_low = min(float(candle["low"]) for _, candle in opening_rows)
    first_open = float(opening_rows[0][1]["open"])
    prior_close = float(prior_rows[-1]["close"])
    current = primary[-1]
    current_close = float(current["close"])
    gap = first_open - prior_close
    gap_aligned = (
        atr > 0
        and abs(gap) >= 0.25 * atr
        and ((direction == "LONG" and gap > 0) or (direction == "SHORT" and gap < 0))
    )
    broke_range = (
        current_close > opening_high
        if direction == "LONG"
        else current_close < opening_low
    )
    accepted = (
        current_close > float(current["open"])
        if direction == "LONG"
        else current_close < float(current["open"])
    )
    after_range = current_time > opening_rows[-1][0]
    in_session = start_hour <= current_time.hour < end_hour
    predicates += (
        _predicate(
            "opening_range_breakout",
            broke_range,
            current_close,
            f"{direction} close beyond [{opening_low}, {opening_high}]",
        ),
        _predicate(
            "aligned_opening_gap",
            gap_aligned,
            round(gap / atr, 4) if atr > 0 else None,
            f"{direction} gap >= 0.25 ATR",
        ),
        _predicate(
            "after_opening_range",
            after_range,
            current_time.isoformat(),
            "after first 2 confirmed session bars",
        ),
        _predicate("continuation_close", accepted, current_close, f"{direction} candle"),
        _predicate("active_session_utc", in_session, current_time.isoformat(), label),
    )
    continuation = (broke_range or gap_aligned) and after_range and in_session
    if continuation and accepted:
        return SetupCandidate(setup_id, "TRADE", direction, predicates, ())
    if continuation:
        return SetupCandidate(
            setup_id,
            "WATCH",
            direction,
            predicates,
            ("continuation_close_missing",),
        )
    reasons = tuple(predicate.name for predicate in predicates if not predicate.passed)
    return SetupCandidate(setup_id, "NO_SIGNAL", direction, predicates, reasons)


def _with_relative_strength(
    candidate: SetupCandidate,
    primary: list[dict],
) -> SetupCandidate:
    closes = [float(candle["close"]) for candle in primary[-20:]]
    path = sum(abs(current - previous) for previous, current in zip(closes[:-1], closes[1:]))
    net = closes[-1] - closes[0] if len(closes) >= 2 else 0.0
    efficiency = abs(net) / path if path > 0 else 0.0
    aligned = (
        (candidate.direction == "LONG" and net > 0)
        or (candidate.direction == "SHORT" and net < 0)
    )
    passed = len(closes) == 20 and aligned and efficiency >= 0.35
    predicates = candidate.predicates + (
        _predicate(
            "relative_strength_efficiency_20",
            passed,
            round(efficiency, 4),
            "directional 20-bar efficiency >= 0.35",
        ),
    )
    if passed:
        return SetupCandidate(
            candidate.setup_id,
            candidate.decision,
            candidate.direction,
            predicates,
            candidate.rejection_reasons,
        )
    return SetupCandidate(
        candidate.setup_id,
        "NO_SIGNAL",
        candidate.direction,
        predicates,
        tuple(
            dict.fromkeys(
                candidate.rejection_reasons + ("relative_strength_efficiency_failed",)
            )
        ),
    )


def detect_setup(
    route: SpecialistRoute,
    horizon: str,
    candles: dict[str, list[dict]],
) -> SetupCandidate:
    if route.family == "unknown":
        return SetupCandidate(
            "unsupported_specialist",
            "NO_SIGNAL",
            None,
            (_predicate("supported_route", False, route.score_group, "known specialist"),),
            ("unsupported_specialist",),
        )

    primary = candles["H1"] if horizon == "intraday" else candles["H4"]
    context = candles["H4"] if horizon == "intraday" else candles["D1"]
    setup_ids = route.setup_ids(horizon)

    if route.family == "forex":
        candidates = (
            _with_session_gate(
                _breakout_retest_candidate(
                    setup_ids[0],
                    primary,
                    context,
                    require_contraction=False,
                ),
                primary,
                route,
            ),
            _pullback_candidate(setup_ids[-1], primary, context),
        )
    elif route.family == "crypto":
        candidates = (
            _breakout_retest_candidate(setup_ids[0], primary, context, require_contraction=True),
            _pullback_candidate(setup_ids[-1], primary, context),
        )
    elif route.subclass in {"xau", "precious"}:
        candidates = (
            _with_session_gate(
                _breakout_retest_candidate(
                    setup_ids[0],
                    primary,
                    context,
                    require_contraction=False,
                    lookback=24,
                ),
                primary,
                route,
            )
            if horizon == "intraday"
            else _pullback_candidate(setup_ids[0], primary, context),
        )
    elif route.family == "commodity":
        parameters = {
            "energy_oil": (16, 0.45, 1.35),
            "nat_gas": (12, 0.60, 1.50),
            "copper": (20, 0.40, 1.25),
            "pgm_metals": (24, 0.45, 1.25),
            "base_metals": (24, 0.40, 1.20),
            "softs": (18, 0.55, 1.40),
            "commodity_other": (20, 0.35, 1.25),
        }
        lookback, touch_distance, max_extension = parameters.get(
            route.subclass,
            (20, 0.35, 1.25),
        )
        candidates = (
            _breakout_retest_candidate(
                setup_ids[0],
                primary,
                context,
                require_contraction=False,
                lookback=lookback,
            )
            if horizon == "intraday"
            else _pullback_candidate(
                setup_ids[0],
                primary,
                context,
                touch_distance_atr=touch_distance,
                max_extension_atr=max_extension,
            ),
        )
    else:
        if horizon == "intraday":
            candidates = (
                _opening_range_gap_candidate(
                    setup_ids[0],
                    primary,
                    context,
                    route,
                ),
            )
        else:
            candidates = tuple(
                _with_relative_strength(candidate, primary)
                for candidate in (
                    _breakout_retest_candidate(
                        setup_ids[0],
                        primary,
                        context,
                        require_contraction=False,
                        lookback=40,
                    ),
                    _pullback_candidate(setup_ids[0], primary, context),
                )
            )

    priority = {"TRADE": 2, "WATCH": 1, "NO_SIGNAL": 0}
    return max(candidates, key=lambda candidate: priority[candidate.decision])


def atr_for_levels(candles: list[dict]) -> float:
    return _atr(candles)
