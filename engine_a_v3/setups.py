from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from statistics import fmean
from typing import Any, Iterable, Mapping

from engine_a_v3.contract import PredicateResult
from engine_a_v3.routing import SpecialistRoute
from engine_a_v3.session_forex import (
    asian_session_range,
    in_london_open_window,
    parse_utc,
    session_quality_score,
    volume_spike_ratio,
)


@dataclass(frozen=True)
class SetupCandidate:
    setup_id: str | None
    decision: str
    direction: str | None
    predicates: tuple[PredicateResult, ...]
    rejection_reasons: tuple[str, ...]
    level_style: str = "trend"  # "trend" | "mean_reversion" | "london_open"


@dataclass(frozen=True)
class SetupPeriods:
    """Group-resolved indicator periods shared with the quant scorer."""

    ema_trend: int = 21
    ema_momentum: int = 50
    atr: int = 14

    @classmethod
    def from_mapping(cls, periods: Mapping[str, Any] | None) -> "SetupPeriods":
        if not periods:
            return cls()
        try:
            return cls(
                ema_trend=int(periods.get("ema_trend", 21) or 21),
                ema_momentum=int(periods.get("ema_momentum", 50) or 50),
                atr=int(periods.get("atr", 14) or 14),
            )
        except (TypeError, ValueError):
            return cls()


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
    """Wilder ATR (same algorithm as indicators.calc_atr) — last valid value."""
    if len(candles) < 2 or period <= 0:
        return 0.0
    highs = [float(c["high"]) for c in candles]
    lows = [float(c["low"]) for c in candles]
    closes = [float(c["close"]) for c in candles]
    from indicators import calc_atr

    series = calc_atr(highs, lows, closes, period)
    for value in reversed(series):
        if value is not None:
            try:
                out = float(value)
            except (TypeError, ValueError):
                continue
            if out == out and out > 0:  # not NaN
                return out
    return 0.0


def _resolve_setup_candle_frames(
    route: SpecialistRoute,
    horizon: str,
    candles: dict[str, list[dict]],
    *,
    entry_tf_override: str | None = None,
) -> tuple[list[dict], list[dict], str, str]:
    """Primary/context frames aligned with the selected style's quant entry TF."""
    from engine_a_v3.timeframes import (
        resolve_diagnostic_v3_entry_timeframe,
        resolve_v3_entry_timeframe,
    )

    asset_type = (
        "forex"
        if route.family == "forex"
        else "crypto"
        if route.family == "crypto"
        else "commodity"
        if route.family == "commodity"
        else "index"
        if route.family == "index"
        else "stock"
        if route.family == "equity_etf"
        else "other"
    )
    diagnostic_override = resolve_diagnostic_v3_entry_timeframe(entry_tf_override)
    if entry_tf_override is not None and diagnostic_override is None:
        return [], [], "", ""
    if diagnostic_override is not None:
        primary_tf = diagnostic_override
        # Entry timing is lower-TF, while structural context stays aligned to
        # the requested horizon: H4 for intraday and D1 for swing.
        context_tf = "D1" if str(horizon).lower() == "swing" else "H4"
        primary = list(candles.get(primary_tf) or [])
        context = list(candles.get(context_tf) or candles.get("D1") or [])
        return primary, context, primary_tf, context_tf

    primary_tf = resolve_v3_entry_timeframe(route.score_group, asset_type, horizon)
    if primary_tf == "H1":
        context_tf = "H4"
    elif primary_tf == "H4":
        context_tf = "D1"
    elif primary_tf == "D1":
        context_tf = "D1"
    else:
        return [], [], "", ""
    primary = candles.get(primary_tf) or candles.get("H4") or candles.get("H1") or []
    context = candles.get(context_tf) or candles.get("D1") or primary
    return primary, context, primary_tf, context_tf


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


def _trend_direction(
    context: list[dict],
    *,
    periods: SetupPeriods | None = None,
) -> tuple[str | None, tuple[PredicateResult, ...]]:
    periods = periods or SetupPeriods()
    closes = [float(candle["close"]) for candle in context]
    need = max(60, periods.ema_momentum + 10)
    if len(closes) < need:
        return None, (
            _predicate("context_history", False, len(closes), f"at least {need} bars"),
        )
    ema_fast = _ema(closes, periods.ema_trend)
    ema_slow = _ema(closes, periods.ema_momentum)
    long_ok = ema_fast[-1] > ema_slow[-1] and closes[-1] > ema_fast[-1]
    short_ok = ema_fast[-1] < ema_slow[-1] and closes[-1] < ema_fast[-1]
    direction = "LONG" if long_ok else "SHORT" if short_ok else None
    return direction, (
        _predicate(
            "context_trend_long",
            long_ok,
            round(ema_fast[-1] - ema_slow[-1], 8),
            "> 0",
        ),
        _predicate(
            "context_trend_short",
            short_ok,
            round(ema_fast[-1] - ema_slow[-1], 8),
            "< 0",
        ),
    )


def _pullback_candidate(
    setup_id: str,
    primary: list[dict],
    context: list[dict],
    *,
    touch_distance_atr: float = 0.35,
    max_extension_atr: float = 1.25,
    periods: SetupPeriods | None = None,
) -> SetupCandidate:
    periods = periods or SetupPeriods()
    direction, context_predicates = _trend_direction(context, periods=periods)
    closes = [float(candle["close"]) for candle in primary]
    ema_fast = _ema(closes, periods.ema_trend)
    atr = _atr(primary, periods.atr)
    if direction is None or len(primary) < 60 or atr <= 0:
        reasons = ["trend_context_not_directional"] if direction is None else ["insufficient_primary_history"]
        return SetupCandidate(setup_id, "NO_SIGNAL", direction, context_predicates, tuple(reasons))

    previous = primary[-2]
    current = primary[-1]
    ema_now = ema_fast[-1]
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
            f"touch EMA{periods.ema_trend} within {touch_distance_atr:.2f} ATR",
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
    periods: SetupPeriods | None = None,
) -> SetupCandidate:
    periods = periods or SetupPeriods()
    direction, context_predicates = _trend_direction(context, periods=periods)
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
    *,
    periods: SetupPeriods | None = None,
) -> SetupCandidate:
    periods = periods or SetupPeriods()
    direction, context_predicates = _trend_direction(context, periods=periods)
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

    atr = _atr(primary, periods.atr)
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


def _efficiency_ratio(primary: list[dict], window: int = 20) -> tuple[float, float]:
    """Kaufman efficiency ratio over the last `window` closes.

    Returns (efficiency, net_change). efficiency = |net move| / path length in
    [0, 1]; high = persistent/trending, low = choppy/mean-reverting. `net` is
    returned so callers can also test directional alignment.
    """
    closes = [float(candle["close"]) for candle in primary[-window:]]
    if len(closes) < 2:
        return 0.0, 0.0
    path = sum(abs(current - previous) for previous, current in zip(closes[:-1], closes[1:]))
    net = closes[-1] - closes[0]
    return (abs(net) / path if path > 0 else 0.0), net


def _with_relative_strength(
    candidate: SetupCandidate,
    primary: list[dict],
) -> SetupCandidate:
    efficiency, net = _efficiency_ratio(primary, 20)
    aligned = (
        (candidate.direction == "LONG" and net > 0)
        or (candidate.direction == "SHORT" and net < 0)
    )
    passed = len(primary) >= 20 and aligned and efficiency >= 0.35
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


def _mean_reversion_candidate(
    setup_id: str,
    primary: list[dict],
    *,
    z_entry: float = 1.5,
    eff_max: float = 0.35,
    lookback: int = 20,
    periods: SetupPeriods | None = None,
) -> SetupCandidate:
    """Range-fade specialist: in a ranging (low-efficiency) regime, fade a stretch
    away from the EMA mean once the candle turns back toward it. Direction is the
    OPPOSITE of the stretch — counter-trend by design, unlike the trend specialists.
    """
    periods = periods or SetupPeriods()
    if len(primary) < 60:
        return SetupCandidate(
            setup_id, "NO_SIGNAL", None,
            (_predicate("primary_history", False, len(primary), "at least 60 bars"),),
            ("insufficient_primary_history",), "mean_reversion",
        )
    closes = [float(candle["close"]) for candle in primary]
    ema_mean = _ema(closes, periods.ema_trend)
    atr = _atr(primary, periods.atr)
    efficiency, _ = _efficiency_ratio(primary, lookback)
    if atr <= 0:
        return SetupCandidate(
            setup_id, "NO_SIGNAL", None,
            (_predicate("atr_valid", False, atr, "> 0"),),
            ("atr_invalid",), "mean_reversion",
        )
    current = primary[-1]
    previous = primary[-2]
    close = float(current["close"])
    open_ = float(current["open"])
    prev_close = float(previous["close"])
    mean = ema_mean[-1]
    z = (close - mean) / atr
    ranging = efficiency <= eff_max
    direction = "SHORT" if z >= z_entry else "LONG" if z <= -z_entry else None
    reversal = (
        (direction == "SHORT" and close < open_ and close < prev_close)
        or (direction == "LONG" and close > open_ and close > prev_close)
    )
    predicates = (
        _predicate("ranging_regime", ranging, round(efficiency, 4), f"efficiency <= {eff_max:.2f}"),
        _predicate("stretch_from_mean", direction is not None, round(z, 4), f"|z| >= {z_entry:.2f}"),
        _predicate("reversal_confirmation", bool(reversal), close, "candle turns toward mean"),
    )
    if direction is not None and ranging and reversal:
        return SetupCandidate(setup_id, "TRADE", direction, predicates, (), "mean_reversion")
    if direction is not None and ranging:
        return SetupCandidate(
            setup_id, "WATCH", direction, predicates,
            ("reversal_confirmation_missing",), "mean_reversion",
        )
    reasons = tuple(predicate.name for predicate in predicates if not predicate.passed)
    return SetupCandidate(setup_id, "NO_SIGNAL", direction, predicates, reasons, "mean_reversion")


def _london_open_breakout_candidate(
    setup_id: str,
    primary: list[dict],
    *,
    display: str | None = None,
) -> SetupCandidate:
    """Asian range breakout during London open with tick-volume confirmation."""
    if len(primary) < 80:
        return SetupCandidate(
            setup_id,
            "NO_SIGNAL",
            None,
            (_predicate("primary_history", False, len(primary), "at least 80 bars"),),
            ("insufficient_primary_history",),
            "london_open",
        )
    as_of = parse_utc(primary[-1].get("time") or primary[-1].get("datetime"))
    if as_of is None:
        return SetupCandidate(
            setup_id,
            "NO_SIGNAL",
            None,
            (_predicate("timestamp_valid", False, None, "parseable UTC time"),),
            ("timestamp_invalid",),
            "london_open",
        )
    asian = asian_session_range(primary, as_of=as_of)
    if asian is None:
        return SetupCandidate(
            setup_id,
            "NO_SIGNAL",
            None,
            (_predicate("asian_range", False, None, "Asian session 00:00-07:00 UTC"),),
            ("asian_range_missing",),
            "london_open",
        )
    current = primary[-1]
    close = float(current["close"])
    long_break = close > asian.high
    short_break = close < asian.low
    direction = "LONG" if long_break else "SHORT" if short_break else None
    vol_ratio = volume_spike_ratio(primary, display=display, tf="H1")
    vol_ok = vol_ratio >= 1.5
    session_ok = in_london_open_window(as_of)
    quality, quality_label = session_quality_score(as_of)
    quality_ok = quality >= 0.75
    predicates = (
        _predicate("asian_range_defined", True, round(asian.high - asian.low, 6), "> 0"),
        _predicate("london_open_window", session_ok, as_of.isoformat(), "07:00-08:30 UTC"),
        _predicate("session_quality", quality_ok, quality_label, ">= 0.75 gate"),
        _predicate(
            "breakout_direction",
            direction is not None,
            close,
            f"[{asian.low}, {asian.high}]",
        ),
        _predicate("volume_spike", vol_ok, round(vol_ratio, 4), ">= 1.5x 20-bar mean"),
    )
    if direction and session_ok and quality_ok and vol_ok:
        return SetupCandidate(setup_id, "TRADE", direction, predicates, (), "london_open")
    if direction and session_ok and quality_ok:
        return SetupCandidate(
            setup_id,
            "WATCH",
            direction,
            predicates,
            ("volume_spike_missing",),
            "london_open",
        )
    reasons = tuple(predicate.name for predicate in predicates if not predicate.passed)
    return SetupCandidate(setup_id, "NO_SIGNAL", direction, predicates, reasons, "london_open")


def _commodity_setup_params(subclass: str) -> tuple[int, float, float]:
    """Lookback / touch / extension for commodity specialists (config-gated)."""
    defaults = {
        "energy_oil": (16, 0.45, 1.35),
        "nat_gas": (12, 0.60, 1.50),
        "copper": (20, 0.40, 1.25),
        "pgm_metals": (24, 0.45, 1.25),
        "base_metals": (24, 0.40, 1.20),
        "softs": (18, 0.55, 1.40),
        "commodity_other": (20, 0.35, 1.25),
    }
    lookback, touch, extension = defaults.get(subclass, (20, 0.35, 1.25))
    try:
        from config import CONFIG

        keyed = CONFIG.get("ENGINE_A_V3_COMMODITY_SETUP_PARAMS") or {}
        override = keyed.get(subclass) if isinstance(keyed, dict) else None
        if isinstance(override, dict):
            lookback = int(override.get("lookback", lookback) or lookback)
            touch = float(override.get("touch_distance_atr", touch) or touch)
            extension = float(override.get("max_extension_atr", extension) or extension)
    except Exception:
        pass
    return lookback, touch, extension


def detect_setup(
    route: SpecialistRoute,
    horizon: str,
    candles: dict[str, list[dict]],
    *,
    display: str | None = None,
    indicator_periods: Mapping[str, Any] | None = None,
    entry_tf_override: str | None = None,
) -> SetupCandidate:
    if route.family == "unknown":
        return SetupCandidate(
            "unsupported_specialist",
            "NO_SIGNAL",
            None,
            (_predicate("supported_route", False, route.score_group, "known specialist"),),
            ("unsupported_specialist",),
        )

    if indicator_periods is None:
        from engine_a_v3.profile import _resolved_periods

        indicator_periods = _resolved_periods(route.score_group, route.family)
    periods = SetupPeriods.from_mapping(indicator_periods)
    primary, context, _primary_tf, _context_tf = _resolve_setup_candle_frames(
        route, horizon, candles, entry_tf_override=entry_tf_override
    )
    setup_ids = route.setup_ids(horizon)

    if route.family == "forex":
        from config import CONFIG

        thesis = str(CONFIG.get("ENGINE_A_V3_FOREX_THESIS", "trend") or "trend").lower()
        setup_mode = str(CONFIG.get("ENGINE_A_V3_FOREX_SETUP", "trend") or "trend").lower()
        if thesis == "mean_reversion":
            mr = CONFIG.get("ENGINE_A_V3_MEAN_REVERSION") or {}
            candidates = (
                _mean_reversion_candidate(
                    "fx_range_mean_reversion",
                    primary,
                    z_entry=float(mr.get("Z_ENTRY", 1.5)),
                    eff_max=float(mr.get("EFF_MAX", 0.35)),
                    lookback=int(mr.get("LOOKBACK", 20)),
                    periods=periods,
                ),
            )
        elif setup_mode == "london_open":
            candidates = (_london_open_breakout_candidate("fx_london_open_breakout", primary, display=display),)
        else:
            candidates = (
                _with_session_gate(
                    _breakout_retest_candidate(
                        setup_ids[0],
                        primary,
                        context,
                        require_contraction=False,
                        periods=periods,
                    ),
                    primary,
                    route,
                ),
                _pullback_candidate(
                    setup_ids[-1], primary, context, periods=periods
                ),
            )
    elif route.family == "crypto":
        candidates = (
            _breakout_retest_candidate(
                setup_ids[0],
                primary,
                context,
                require_contraction=True,
                periods=periods,
            ),
            _pullback_candidate(setup_ids[-1], primary, context, periods=periods),
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
                    periods=periods,
                ),
                primary,
                route,
            )
            if horizon == "intraday"
            else _pullback_candidate(
                setup_ids[0], primary, context, periods=periods
            ),
        )
    elif route.family == "commodity":
        lookback, touch_distance, max_extension = _commodity_setup_params(route.subclass)
        candidates = (
            _breakout_retest_candidate(
                setup_ids[0],
                primary,
                context,
                require_contraction=False,
                lookback=lookback,
                periods=periods,
            )
            if horizon == "intraday"
            else _pullback_candidate(
                setup_ids[0],
                primary,
                context,
                touch_distance_atr=touch_distance,
                max_extension_atr=max_extension,
                periods=periods,
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
                    periods=periods,
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
                        periods=periods,
                    ),
                    _pullback_candidate(
                        setup_ids[0], primary, context, periods=periods
                    ),
                )
            )

    priority = {"TRADE": 2, "WATCH": 1, "NO_SIGNAL": 0}
    return max(candidates, key=lambda candidate: priority[candidate.decision])


def atr_for_levels(candles: list[dict], period: int = 14) -> float:
    """Wilder ATR for SL/TP geometry — matches indicators.calc_atr / quant bundle."""
    return _atr(candles, period)
