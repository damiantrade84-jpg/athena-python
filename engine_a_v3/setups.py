from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from statistics import fmean
from typing import Any, Iterable, Mapping

from engine_a_v3.contract import PredicateResult
from engine_a_v3.routing import SpecialistRoute, intradaily_pullback_overlay_enabled
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
    # Bars back from the newest confirmed bar at which the triggering pattern
    # fired (0 = newest). None when the candidate did not fire inside the
    # lookback window or lookback is disabled.
    trigger_bar_age: int | None = None


def _setup_trigger_offsets(primary_len: int) -> tuple[int, ...]:
    """Bar ages to scan for setup triggers, newest -> oldest.

    Mirrors the Engine B trigger lookback (market_structure._price_action_
    trigger): a valid pattern on any of the last N closed bars counts, not
    only the newest one. ENGINE_A_V3_SETUP_LOOKBACK_BARS = 1 restores the
    legacy last-bar-only behaviour exactly.
    """
    try:
        from config import CONFIG

        if not bool(CONFIG.get("ENGINE_A_V3_SETUP_LOOKBACK_ENABLED", True)):
            return (0,)
        bars = int(CONFIG.get("ENGINE_A_V3_SETUP_LOOKBACK_BARS", 3) or 3)
    except Exception:
        bars = 3
    bars = max(1, min(bars, 10))
    return tuple(age for age in range(bars) if primary_len - age >= 2) or (0,)


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


def _quiet_window_extra_bars(
    candles: list[dict],
    atr: float | None,
    *,
    base: int = 20,
    max_extra: int = 8,
    compress_ratio: float = 0.7,
) -> int:
    """Extra structural-window bars when the trailing range is compressed.

    Mirrors the Engine B BOS-lookback widening (market_structure.
    _engine_b_quiet_window_extra_bars): in quiet regimes average true range
    collapses relative to the structure ATR, so the structural reference (SL
    anchor or breakout level) can sit beyond the fixed window. Returns a
    bounded number of extra bars scaled by the measured compression, or 0 when
    no compression is detected / data is too short, so default behavior is
    unchanged.
    """
    try:
        if float(compress_ratio) <= 0 or float(max_extra) <= 0:
            return 0
        window = max(int(base), 1)
        if len(candles) < window + 1:
            return 0
        highs = [float(c["high"]) for c in candles]
        lows = [float(c["low"]) for c in candles]
        closes = [float(c["close"]) for c in candles]
        tr = [
            max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
            for i in range(1, len(candles))
        ]
        recent = float(fmean(tr[-window:])) if tr[-window:] else 0.0
        try:
            anchor = float(atr) if atr is not None else 0.0
        except (TypeError, ValueError):
            anchor = 0.0
        if anchor <= 0:
            anchor = float(fmean(tr)) if tr else 0.0
        if anchor <= 0 or recent >= anchor * float(compress_ratio):
            return 0
        compression = min(1.0, 1.0 - (recent / anchor))
        return int(round(compression * float(max_extra)))
    except Exception:
        return 0


def _atr(
    candles: list[dict],
    period: int = 14,
    *,
    series_cache: "SetupSeriesCache | None" = None,
    timeframe: str | None = None,
) -> float:
    """Wilder ATR (same algorithm as indicators.calc_atr) — last valid value."""
    if (
        series_cache is not None
        and timeframe is not None
        and series_cache.matches(timeframe, candles)
    ):
        cached = series_cache.atr_at(timeframe, len(candles), period)
        if cached is not None:
            return cached
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


def _ema_last(
    values: Iterable[float],
    period: int,
    *,
    series_cache: "SetupSeriesCache | None" = None,
    timeframe: str | None = None,
    candles: list[dict] | None = None,
) -> float:
    """Last EMA value — served from a full-series precompute when available.

    ``candles`` is the frame the values were derived from; the cache path is
    taken only when that frame verifiably is a prefix of the cached series
    (guards against the frame-resolution fallbacks that substitute another
    timeframe's rows under the requested name).
    """
    if (
        series_cache is not None
        and timeframe is not None
        and candles is not None
        and series_cache.matches(timeframe, candles)
    ):
        values_list = values if isinstance(values, list) else list(values)
        if len(values_list) == len(candles):
            cached = series_cache.ema_at(timeframe, len(values_list), period)
            if cached is not None:
                return cached
        values = values_list
    arr = _ema(values, period)
    return arr[-1] if arr else 0.0


class SetupSeriesCache:
    """Full-series EMA/ATR precompute for point-in-time V3 backtest setups.

    Mirrors ``indicator_adapter.BacktestIndicatorSnapshotCache``: built once
    from the full available history per timeframe (causal recurrences don't
    change with future bars), then reduced to an O(1) at-length lookup instead
    of recomputing from scratch on every backtest bar. Caller-owned and
    scoped to one backtest run; never constructed by live scanning, so
    ``detect_setup``'s default (``series_cache=None``) is byte-identical to
    the pre-existing behavior.
    """

    def __init__(self, candles: Mapping[str, list[dict]]):
        self._candles = candles
        self._ema_series: dict[tuple[str, int], list[float]] = {}
        self._atr_series: dict[tuple[str, int], list[float]] = {}
        self._calc_ema_series: dict[tuple[str, int], list] = {}
        self._vwap_prefix: dict[str, tuple[list[float], list[float]]] = {}

    def matches(self, timeframe: str, candles: list[dict] | None) -> bool:
        """True when `candles` verifiably is a prefix of the cached series.

        Frame resolution can substitute another timeframe's rows under the
        requested name (`candles.get(tf) or candles.get("H4") ...`), and
        callers may hold prefix *copies*; comparing the last bar's open time
        at the same index is an O(1) check that accepts prefixes and copies
        of the cached series while rejecting cross-timeframe substitutions.
        """
        rows = self._candles.get(timeframe) or []
        n = len(candles) if candles else 0
        if n <= 0 or n > len(rows):
            return False
        try:
            return rows[n - 1].get("time") == candles[-1].get("time")
        except (AttributeError, TypeError):
            return False

    def ema_series_view(self, timeframe: str, length: int, period: int) -> list[float] | None:
        """Shared full-series `_ema` array (do NOT mutate).

        Values at indices < `length` equal `_ema` over that prefix (EMA is a
        causal recurrence). Caller must not read indices >= length.
        """
        rows = self._candles.get(timeframe) or []
        if length <= 0 or length > len(rows):
            return None
        key = (timeframe, period)
        arr = self._ema_series.get(key)
        if arr is None:
            closes = [float(c["close"]) for c in rows]
            arr = _ema(closes, period)
            self._ema_series[key] = arr
        return arr if length <= len(arr) else None

    def calc_ema_series_view(self, timeframe: str, length: int, period: int) -> list | None:
        """Shared full-series `indicators.calc_ema` array (do NOT mutate).

        Same causal-prefix identity and index contract as `ema_series_view`.
        """
        rows = self._candles.get(timeframe) or []
        if length <= 0 or length > len(rows):
            return None
        key = (timeframe, period)
        arr = self._calc_ema_series.get(key)
        if arr is None:
            from indicators import calc_ema

            closes = [float(c["close"]) for c in rows]
            arr = calc_ema(closes, period)
            self._calc_ema_series[key] = arr
        return arr if length <= len(arr) else None

    def ema_at(self, timeframe: str, length: int, period: int) -> float | None:
        rows = self._candles.get(timeframe) or []
        if length <= 0 or length > len(rows):
            return None
        key = (timeframe, period)
        arr = self._ema_series.get(key)
        if arr is None:
            closes = [float(c["close"]) for c in rows]
            arr = _ema(closes, period)
            self._ema_series[key] = arr
        return arr[length - 1] if arr and length - 1 < len(arr) else None

    def atr_at(self, timeframe: str, length: int, period: int) -> float | None:
        rows = self._candles.get(timeframe) or []
        if length <= 0 or length > len(rows) or length < 2 or period <= 0:
            return None
        key = (timeframe, period)
        arr = self._atr_series.get(key)
        if arr is None:
            highs = [float(c["high"]) for c in rows]
            lows = [float(c["low"]) for c in rows]
            closes = [float(c["close"]) for c in rows]
            from indicators import calc_atr

            arr = calc_atr(highs, lows, closes, period)
            self._atr_series[key] = arr
        for index in range(min(length, len(arr)) - 1, -1, -1):
            value = arr[index]
            if value is None:
                continue
            try:
                out = float(value)
            except (TypeError, ValueError):
                continue
            if out == out and out > 0:  # not NaN
                return out
        return 0.0

    def rolling_vwap_at(
        self, timeframe: str, length: int, lookback: int
    ) -> float | None:
        """Final anchored VWAP over the requested causal trailing window."""
        rows = self._candles.get(timeframe) or []
        if length <= 0 or length > len(rows) or lookback <= 0:
            return None
        cached = self._vwap_prefix.get(timeframe)
        if cached is None:
            cumulative_volume = [0.0]
            cumulative_typical_volume = [0.0]
            for candle in rows:
                close = float(candle.get("close", 0))
                typical_price = (
                    float(candle.get("high", close))
                    + float(candle.get("low", close))
                    + close
                ) / 3.0
                volume = float(candle.get("vol", 0) or 0.0)
                cumulative_volume.append(
                    cumulative_volume[-1] + (volume if volume > 0 else 0.0)
                )
                cumulative_typical_volume.append(
                    cumulative_typical_volume[-1]
                    + (typical_price * volume if volume > 0 else 0.0)
                )
            cached = cumulative_volume, cumulative_typical_volume
            self._vwap_prefix[timeframe] = cached
        start = max(0, length - lookback)
        volume = cached[0][length] - cached[0][start]
        if volume <= 0:
            return None
        typical_volume = cached[1][length] - cached[1][start]
        return round(typical_volume / volume, 8)


def _resolve_setup_candle_frames(
    route: SpecialistRoute,
    horizon: str,
    candles: dict[str, list[dict]],
    *,
    entry_tf_override: str | None = None,
    structure_tf: str | None = None,
) -> tuple[list[dict], list[dict], str, str]:
    """Primary/context frames aligned with the selected style's quant entry TF.

    ``structure_tf`` is the policy structure role. When supplied it becomes
    the setup overlay's context series instead of the hardcoded one-rung-slower
    fallback (so equity M30 setup reads H1 structure, not H4).
    """
    from engine_a_v3.timeframes import (
        resolve_setup_context_timeframe,
        resolve_setup_primary_timeframe,
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
    requested_tf = resolve_setup_primary_timeframe(entry_tf_override)
    if entry_tf_override is not None and requested_tf is None:
        return [], [], "", ""
    if requested_tf is not None:
        primary_tf = requested_tf
        # Entry timing may be lower-TF, while structural context stays one rung
        # slower: H4 for intraday/lower primaries, D1 for swing and for an H4
        # primary (which SLOW speed adaptation can produce).
        context_tf = resolve_setup_context_timeframe(
            primary_tf, horizon, structure_tf=structure_tf
        )
        primary = candles.get(primary_tf) or []
        context = candles.get(context_tf) or candles.get("D1") or []
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


def _dst_shifted_utc_window(
    start_hour: int,
    end_hour: int,
    on_time: "datetime | None",
    tz_name: str,
    base_offset_hours: float,
) -> tuple[float, float] | None:
    """Shift a fixed-UTC session window to the venue's local offset on a date.

    The fixed tables below were calibrated against one DST regime (US windows
    against America/New_York EDT, UTC-4). Around DST transitions the exchange's
    real cash session moves in UTC while the table does not, so the setup gate
    and the quant scorer's calendar-based gate disagree for weeks. When the bar
    time and the timezone database are available, re-express the window at the
    venue's actual offset for that date. Returns None when it cannot (caller
    keeps the fixed window) — never guesses.
    """
    if on_time is None:
        return None
    try:
        from zoneinfo import ZoneInfo

        local = on_time.astimezone(ZoneInfo(tz_name))
        offset = local.utcoffset()
        if offset is None:
            return None
        delta = offset.total_seconds() / 3600.0 - base_offset_hours
        shifted_start = start_hour + delta
        shifted_end = end_hour + delta
        if not (0 <= shifted_start < 24 and 0 < shifted_end <= 24):
            return None
        return shifted_start, shifted_end
    except Exception:
        return None


def _session_window(
    route: SpecialistRoute,
    display: str | None = None,
    on_time: "datetime | None" = None,
) -> tuple[float, float, str]:
    """(start_hour, end_hour, label) UTC window for the route's cash session.

    ``on_time`` (the trigger bar's timestamp) enables DST tracking where the
    local-hours mapping is unambiguous. Only the US cash session qualifies
    today (13:00-21:00 UTC == 09:00-17:00 America/New_York at UTC-4); the EU /
    Asian / JSE windows stay fixed-UTC approximations — their original local
    calibration is ambiguous, so shifting them would be a guess, and the quant
    scorer discloses the same approximation for eu_indices (XETRA proxy).
    """
    if route.family == "forex":
        return 6, 21, "London/NY 06:00-21:00 UTC"
    if route.subclass in {"xau", "precious"}:
        return 6, 21, "London/NY 06:00-21:00 UTC"
    us_session = route.subclass in {
        "us_indices",
        "us_stock_single",
        "bond_tlt",
        "smallcap_em_etf",
    } or str(display or "").strip().upper() in {
        "US2000",
        "US 2000",
        "RUSSELL 2000",
        "RUSSELL",
    }
    if us_session:
        start, end, label = 13, 21, "US cash session 13:00-21:00 UTC"
        try:
            from config import CONFIG

            dst_aware = bool(CONFIG.get("ENGINE_A_V3_SESSION_DST_AWARE", True))
        except Exception:
            dst_aware = True
        if dst_aware:
            shifted = _dst_shifted_utc_window(
                start, end, on_time, "America/New_York", -4.0
            )
            if shifted is not None:
                return shifted[0], shifted[1], label
        return start, end, label
    if route.subclass == "eu_indices":
        return 7, 17, "European cash session 07:00-17:00 UTC"
    if route.subclass == "asian_indices":
        return 0, 9, "Asian cash session 00:00-09:00 UTC"
    if route.subclass == "jse_equity":
        # SAST is UTC+2 year-round (no DST): the fixed window cannot drift.
        return 7, 16, "JSE cash session 07:00-16:00 UTC"
    return 6, 18, "regional cash session 06:00-18:00 UTC"


def _with_session_gate(
    candidate: SetupCandidate,
    primary: list[dict],
    route: SpecialistRoute,
    display: str | None = None,
) -> SetupCandidate:
    current_time = _parse_time(primary[-1].get("time") or primary[-1].get("datetime"))
    start_hour, end_hour, label = _session_window(route, display, on_time=current_time)
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
            level_style=candidate.level_style,
            trigger_bar_age=candidate.trigger_bar_age,
        )
    return SetupCandidate(
        candidate.setup_id,
        "NO_SIGNAL",
        candidate.direction,
        predicates,
        tuple(dict.fromkeys(candidate.rejection_reasons + ("outside_active_session_utc",))),
        level_style=candidate.level_style,
        trigger_bar_age=candidate.trigger_bar_age,
    )


def _trend_direction(
    context: list[dict],
    *,
    periods: SetupPeriods | None = None,
    series_cache: "SetupSeriesCache | None" = None,
    context_tf: str | None = None,
) -> tuple[str | None, tuple[PredicateResult, ...]]:
    periods = periods or SetupPeriods()
    need = max(60, periods.ema_momentum + 10)
    if len(context) < need:
        return None, (
            _predicate("context_history", False, len(context), f"at least {need} bars"),
        )
    ema_fast_last = None
    ema_slow_last = None
    if (
        series_cache is not None
        and context_tf is not None
        and series_cache.matches(context_tf, context)
    ):
        ema_fast_last = series_cache.ema_at(
            context_tf, len(context), periods.ema_trend
        )
        ema_slow_last = series_cache.ema_at(
            context_tf, len(context), periods.ema_momentum
        )
    if ema_fast_last is None or ema_slow_last is None:
        closes = [float(candle["close"]) for candle in context]
        ema_fast_last = _ema_last(closes, periods.ema_trend)
        ema_slow_last = _ema_last(closes, periods.ema_momentum)
    latest_close = float(context[-1]["close"])
    long_ok = ema_fast_last > ema_slow_last and latest_close > ema_fast_last
    short_ok = ema_fast_last < ema_slow_last and latest_close < ema_fast_last
    direction = "LONG" if long_ok else "SHORT" if short_ok else None
    return direction, (
        _predicate(
            "context_trend_long",
            long_ok,
            round(ema_fast_last - ema_slow_last, 8),
            "> 0",
        ),
        _predicate(
            "context_trend_short",
            short_ok,
            round(ema_fast_last - ema_slow_last, 8),
            "< 0",
        ),
    )


def _pullback_rejection_required(family: str | None) -> tuple[bool, float]:
    """Whether pullback TRADE needs a rejection wick on the touch bar.

    Per-family via ENGINE_A_V3_PULLBACK_REJECTION.BY_FAMILY. A missing family
    key is off. Failed rejection is WATCH, not a pair demotion.
    """
    try:
        from config import CONFIG

        cfg = CONFIG.get("ENGINE_A_V3_PULLBACK_REJECTION") or {}
        if not isinstance(cfg, Mapping) or not bool(cfg.get("ENABLED", False)):
            return False, 0.0
        by_family = cfg.get("BY_FAMILY") or {}
        family_key = str(family or "").strip().lower()
        if not isinstance(by_family, Mapping) or not family_key:
            return False, 0.0
        if family_key not in by_family or not bool(by_family.get(family_key)):
            return False, 0.0
        wick = float(cfg.get("WICK_BODY_MIN", 1.0) or 1.0)
        return True, max(0.0, wick)
    except Exception:
        return False, 0.0


def _rejection_wick_ok(candle: Mapping[str, Any], direction: str, min_ratio: float) -> bool:
    try:
        open_px = float(candle["open"])
        high_px = float(candle["high"])
        low_px = float(candle["low"])
        close_px = float(candle["close"])
    except (KeyError, TypeError, ValueError):
        return False
    span = high_px - low_px
    if span <= 0:
        return False
    body = abs(close_px - open_px)
    if body <= 0:
        body = span * 0.01
    wick = (min(open_px, close_px) - low_px) if direction == "LONG" else (
        high_px - max(open_px, close_px)
    )
    return wick + 1e-12 >= float(min_ratio) * body


def _pullback_candidate(
    setup_id: str,
    primary: list[dict],
    context: list[dict],
    *,
    touch_distance_atr: float = 0.35,
    max_extension_atr: float = 1.25,
    periods: SetupPeriods | None = None,
    series_cache: "SetupSeriesCache | None" = None,
    primary_tf: str | None = None,
    context_tf: str | None = None,
    family: str | None = None,
) -> SetupCandidate:
    periods = periods or SetupPeriods()
    direction, context_predicates = _trend_direction(
        context, periods=periods, series_cache=series_cache, context_tf=context_tf
    )
    ema_now = None
    if (
        series_cache is not None
        and primary_tf is not None
        and series_cache.matches(primary_tf, primary)
    ):
        ema_now = series_cache.ema_at(primary_tf, len(primary), periods.ema_trend)
    if ema_now is None:
        ema_now = _ema_last(
            [float(candle["close"]) for candle in primary], periods.ema_trend
        )
    atr = _atr(primary, periods.atr, series_cache=series_cache, timeframe=primary_tf)
    if direction is None or len(primary) < 60 or atr <= 0:
        reasons = ["trend_context_not_directional"] if direction is None else ["insufficient_primary_history"]
        return SetupCandidate(setup_id, "NO_SIGNAL", direction, context_predicates, tuple(reasons))

    reject_required, wick_min = _pullback_rejection_required(family)
    watch_fallback: SetupCandidate | None = None
    no_signal_fallback: SetupCandidate | None = None
    # Trigger lookback: evaluate the touch/confirm pair on each of the last N
    # closed bars (newest first). EMA/ATR are re-anchored per offset so an
    # older trigger is judged against the mean it actually pulled back to.
    for age in _setup_trigger_offsets(len(primary)):
        scan = primary[: len(primary) - age]
        previous = scan[-2]
        current = scan[-1]
        if age == 0:
            ema_at_bar = ema_now
            atr_at_bar = atr
        else:
            ema_at_bar = None
            if (
                series_cache is not None
                and primary_tf is not None
                and series_cache.matches(primary_tf, scan)
            ):
                ema_at_bar = series_cache.ema_at(primary_tf, len(scan), periods.ema_trend)
            if ema_at_bar is None:
                ema_at_bar = _ema_last(
                    [float(candle["close"]) for candle in scan], periods.ema_trend
                )
            atr_at_bar = _atr(scan, periods.atr)
            if atr_at_bar <= 0:
                continue
        touched = (
            float(previous["low"]) <= ema_at_bar + touch_distance_atr * atr_at_bar
            if direction == "LONG"
            else float(previous["high"]) >= ema_at_bar - touch_distance_atr * atr_at_bar
        )
        confirmed = (
            float(current["close"]) > ema_at_bar and float(current["close"]) > float(current["open"])
            if direction == "LONG"
            else float(current["close"]) < ema_at_bar and float(current["close"]) < float(current["open"])
        )
        extension = abs(float(current["close"]) - ema_at_bar) <= max_extension_atr * atr_at_bar
        rejected = (not reject_required) or _rejection_wick_ok(previous, direction, wick_min)
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
                round(abs(float(current["close"]) - ema_at_bar) / atr_at_bar, 4),
                f"<= {max_extension_atr:.2f} ATR",
            ),
        )
        if reject_required:
            predicates = predicates + (
                _predicate(
                    "pullback_rejection_wick",
                    rejected,
                    previous["low"] if direction == "LONG" else previous["high"],
                    f"touch-bar rejection wick >= {wick_min:.2f}x body",
                ),
            )
        if touched and confirmed and extension and rejected:
            return SetupCandidate(setup_id, "TRADE", direction, predicates, (), trigger_bar_age=age)
        if touched and extension:
            reasons = []
            if not confirmed:
                reasons.append("confirmation_close_missing")
            if reject_required and not rejected:
                reasons.append("pullback_rejection_missing")
            fallback = SetupCandidate(
                setup_id,
                "WATCH",
                direction,
                predicates,
                tuple(reasons) or ("confirmation_close_missing",),
                trigger_bar_age=age,
            )
            if watch_fallback is None:
                watch_fallback = fallback
        if no_signal_fallback is None:
            reasons = tuple(predicate.name for predicate in predicates if not predicate.passed)
            no_signal_fallback = SetupCandidate(
                setup_id, "NO_SIGNAL", direction, predicates, reasons
            )
    if watch_fallback is not None:
        return watch_fallback
    if no_signal_fallback is not None:
        return no_signal_fallback
    reasons = tuple(
        predicate.name for predicate in context_predicates if not predicate.passed
    )
    return SetupCandidate(setup_id, "NO_SIGNAL", direction, context_predicates, reasons)


def _breakout_retest_candidate(
    setup_id: str,
    primary: list[dict],
    context: list[dict],
    *,
    require_contraction: bool,
    lookback: int = 20,
    periods: SetupPeriods | None = None,
    series_cache: "SetupSeriesCache | None" = None,
    context_tf: str | None = None,
) -> SetupCandidate:
    periods = periods or SetupPeriods()
    direction, context_predicates = _trend_direction(
        context, periods=periods, series_cache=series_cache, context_tf=context_tf
    )
    if direction is None or len(primary) < 60:
        return SetupCandidate(
            setup_id,
            "NO_SIGNAL",
            direction,
            context_predicates,
            ("trend_context_not_directional",),
        )
    effective_lookback = max(int(lookback), 1)
    try:
        from config import CONFIG

        # F4: quiet/compressed regimes compress ranges and can pull the structural
        # reference (breakout level) beyond the fixed window. When enabled, widen
        # the reference window in such regimes (bounded by an absolute cap).
        # Default off so detection is unchanged until the operator opts in.
        adaptive_structure = bool(
            CONFIG.get("ENGINE_A_V3_ADAPTIVE_STRUCTURE_WINDOW_ENABLED", False)
        )
        max_extra = int(CONFIG.get("ENGINE_A_V3_STRUCTURE_WINDOW_MAX_EXTRA_BARS", 8) or 8)
        compress_ratio = float(CONFIG.get("ENGINE_A_V3_STRUCTURE_WINDOW_COMPRESS_RATIO", 0.7) or 0.7)
    except Exception:
        adaptive_structure = False
        max_extra = 8
        compress_ratio = 0.7
    # Breakout/retest window. The pattern used to be pinned to exactly two bars
    # (breakout == primary[-2], retest == primary[-1]), so it only ever fired
    # when a level was broken and retested on consecutive bars — a real retest
    # normally takes several. The breakout bar is now searched back over
    # ENGINE_A_V3_BREAKOUT_RETEST_MAX_BARS, with the retest/acceptance bar
    # itself scanned over the trigger lookback window (newest first), and the
    # level required to have held for every bar in between (no close back
    # through it). MAX_BARS = 1 plus lookback 1 restores the legacy two-bar
    # behaviour exactly.
    try:
        from config import CONFIG

        retest_max_bars = int(CONFIG.get("ENGINE_A_V3_BREAKOUT_RETEST_MAX_BARS", 5) or 5)
    except Exception:
        retest_max_bars = 5

    is_long = direction == "LONG"
    watch_fallback: SetupCandidate | None = None
    no_signal_fallback: SetupCandidate | None = None

    def _broken_swing(prior_rows: list, breakout_row: dict) -> float | None:
        # F2: anchor the reference on the highest (LONG) / lowest (SHORT) local
        # swing extreme that the breakout bar actually closed through, rather than
        # the raw window extreme — a single outlier wick can set a non-structural
        # level. Falls back to the window extreme when no swing qualifies.
        _ext = (lambda c: float(c["high"])) if is_long else (lambda c: float(c["low"]))
        pivots = []
        for i in range(1, len(prior_rows) - 1):
            cur = _ext(prior_rows[i])
            if is_long:
                if cur >= _ext(prior_rows[i - 1]) and cur >= _ext(prior_rows[i + 1]):
                    pivots.append(cur)
            elif cur <= _ext(prior_rows[i - 1]) and cur <= _ext(prior_rows[i + 1]):
                pivots.append(cur)
        close_now = float(breakout_row["close"])
        for lvl in sorted(set(pivots), reverse=is_long):
            if (close_now > lvl) if is_long else (close_now < lvl):
                return lvl
        return None

    for age in _setup_trigger_offsets(len(primary)):
        scan = primary[: len(primary) - age]
        if len(scan) < 60:
            break
        eff_lookback = effective_lookback
        swing_anchor = False
        if adaptive_structure:
            swing_anchor = True
            _atr_anchor = _atr(scan[:-2], periods.atr)
            eff_lookback = min(
                max(1, len(scan) - 2),
                eff_lookback
                + _quiet_window_extra_bars(
                    scan[:-2],
                    _atr_anchor,
                    base=eff_lookback,
                    max_extra=max_extra,
                    compress_ratio=compress_ratio,
                ),
            )
        rbars = max(1, min(retest_max_bars, max(1, len(scan) - eff_lookback - 1)))
        retest = scan[-1]

        def _window_for(offset: int) -> tuple[list, dict]:
            """(prior window, breakout bar) for a breakout `offset` bars back."""
            end = -(offset + 1)
            return scan[-(eff_lookback + offset + 1):end], scan[end]

        def _levels_for(prior_rows: list, breakout_row: dict) -> tuple[float, float]:
            hi = max(float(candle["high"]) for candle in prior_rows)
            lo = min(float(candle["low"]) for candle in prior_rows)
            if swing_anchor:
                ref = _broken_swing(prior_rows, breakout_row)
                if ref is not None:
                    if is_long:
                        hi = ref
                    else:
                        lo = ref
            return hi, lo

        def _level_held(breakout_offset: int, lvl: float) -> bool:
            """No close back through the level between the break and the retest bar."""
            start = len(scan) - breakout_offset  # first bar after the breakout
            for row in scan[start:-1]:
                close_v = float(row["close"])
                if (close_v < lvl) if is_long else (close_v > lvl):
                    return False
            return True

        prior, breakout = _window_for(1)
        high_level, low_level = _levels_for(prior, breakout)
        level = high_level if is_long else low_level
        broke = (
            float(breakout["close"]) > high_level
            if is_long
            else float(breakout["close"]) < low_level
        )
        breakout_bars_ago = 1
        if not broke:
            for _offset in range(2, rbars + 1):
                if len(scan) < eff_lookback + _offset + 2:
                    break
                _prior, _breakout = _window_for(_offset)
                if len(_prior) < 3:
                    break
                _hi, _lo = _levels_for(_prior, _breakout)
                _lvl = _hi if is_long else _lo
                _broke = (
                    float(_breakout["close"]) > _hi
                    if is_long
                    else float(_breakout["close"]) < _lo
                )
                if not _broke or not _level_held(_offset, _lvl):
                    continue
                prior, breakout = _prior, _breakout
                high_level, low_level, level = _hi, _lo, _lvl
                broke = True
                breakout_bars_ago = _offset
                break
        retested = (
            float(retest["low"]) <= level <= float(retest["close"])
            if is_long
            else float(retest["high"]) >= level >= float(retest["close"])
        )
        accepted = (
            float(retest["close"]) > float(retest["open"])
            if direction == "LONG"
            else float(retest["close"]) < float(retest["open"])
        )
        recent_ranges = [float(candle["high"]) - float(candle["low"]) for candle in scan[-12:-2]]
        baseline_ranges = [float(candle["high"]) - float(candle["low"]) for candle in scan[-42:-12]]
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
                f"{lookback}-bar close beyond {level} ({breakout_bars_ago} bar(s) ago)",
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
            return SetupCandidate(
                setup_id, "TRADE", direction, predicates, (), trigger_bar_age=age
            )
        if broke and (contraction or not require_contraction):
            if watch_fallback is None:
                watch_fallback = SetupCandidate(
                    setup_id,
                    "WATCH",
                    direction,
                    predicates,
                    ("retest_not_confirmed",),
                    trigger_bar_age=age,
                )
        if no_signal_fallback is None:
            reasons = tuple(predicate.name for predicate in predicates if not predicate.passed)
            no_signal_fallback = SetupCandidate(
                setup_id, "NO_SIGNAL", direction, predicates, reasons
            )
    if watch_fallback is not None:
        return watch_fallback
    if no_signal_fallback is not None:
        return no_signal_fallback
    reasons = tuple(
        predicate.name for predicate in context_predicates if not predicate.passed
    )
    return SetupCandidate(setup_id, "NO_SIGNAL", direction, context_predicates, reasons)


def _opening_range_gap_candidate(
    setup_id: str,
    primary: list[dict],
    context: list[dict],
    route: SpecialistRoute,
    *,
    opening_range_h1: list[dict] | None = None,
    periods: SetupPeriods | None = None,
    display: str | None = None,
    series_cache: "SetupSeriesCache | None" = None,
    primary_tf: str | None = None,
    context_tf: str | None = None,
) -> SetupCandidate:
    periods = periods or SetupPeriods()
    direction, context_predicates = _trend_direction(
        context, periods=periods, series_cache=series_cache, context_tf=context_tf
    )
    # The setup was defined on the first two confirmed H1 cash-session bars.
    # Policy setup/entry adaptation may move ``primary`` to M15/M30, but it
    # must not silently shrink the structural opening range.
    range_source = primary if opening_range_h1 is None else opening_range_h1
    range_tf = primary_tf if opening_range_h1 is None else "H1"
    # This setup only ever needs "today's" cash-session bars plus the single
    # prior close before them, both always within the last few days of
    # range_source regardless of timeframe. Scanning the full confirmed
    # prefix here (thousands to tens of thousands of bars deep into a
    # backtest) turned every one of the two loops below into O(history), and
    # since this function runs once per backtest bar, that made the whole
    # backtest O(history^2) -- multi-minute hangs on index/equity pairs
    # (forex/crypto never route through opening-range-gap setups, so this
    # never showed up outside FOMC-audited asset classes). 500 bars covers
    # ~5 trading days even on the fastest realistic primary_tf (M15), far
    # more headroom than the "2 session bars + 1 prior close" this needs.
    recent_source = range_source[-500:]
    watch_fallback: SetupCandidate | None = None
    no_signal_fallback: SetupCandidate | None = None
    # Trigger lookback: the continuation close is judged on each of the last N
    # closed bars (newest first), with the opening range and session window
    # re-anchored to that bar's own date so an older trigger is measured
    # against the session it actually traded in.
    for age in _setup_trigger_offsets(len(primary)):
        scan = primary[: len(primary) - age]
        if len(scan) < 60:
            break
        current = scan[-1]
        current_time = _parse_time(current.get("time") or current.get("datetime"))
        start_hour, end_hour, label = _session_window(
            route, display, on_time=current_time
        )
        session_rows: list[tuple[datetime, dict]] = []
        if current_time is not None:
            for candle in recent_source:
                candle_time = _parse_time(candle.get("time") or candle.get("datetime"))
                if (
                    candle_time is not None
                    and candle_time.date() == current_time.date()
                    and start_hour <= candle_time.hour < end_hour
                ):
                    session_rows.append((candle_time, candle))
        has_history = (
            direction is not None
            and len(scan) >= 60
            and len(range_source) >= 60
            and len(session_rows) >= 2
        )
        predicates = context_predicates + (
            _predicate(
                "opening_range_history",
                has_history,
                len(session_rows),
                f"at least 2 confirmed H1 bars in {label}",
            ),
        )
        if not has_history or current_time is None:
            if no_signal_fallback is None:
                reasons = ["opening_range_history"] if direction is not None else ["trend_context_not_directional"]
                no_signal_fallback = SetupCandidate(
                    setup_id, "NO_SIGNAL", direction, predicates, tuple(reasons)
                )
            continue

        opening_rows = session_rows[:2]
        opening_start = opening_rows[0][0]
        prior_rows = [
            candle
            for candle in recent_source
            if (_parse_time(candle.get("time") or candle.get("datetime")) or opening_start)
            < opening_start
        ]
        if not prior_rows:
            if no_signal_fallback is None:
                no_signal_fallback = SetupCandidate(
                    setup_id,
                    "NO_SIGNAL",
                    direction,
                    predicates
                    + (_predicate("prior_session_close", False, None, "confirmed close before cash open"),),
                    ("prior_session_close_missing",),
                )
            continue

        atr = _atr(
            range_source,
            periods.atr,
            series_cache=series_cache,
            timeframe=range_tf,
        )
        opening_high = max(float(candle["high"]) for _, candle in opening_rows)
        opening_low = min(float(candle["low"]) for _, candle in opening_rows)
        first_open = float(opening_rows[0][1]["open"])
        prior_close = float(prior_rows[-1]["close"])
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
            return SetupCandidate(
                setup_id, "TRADE", direction, predicates, (), trigger_bar_age=age
            )
        if continuation:
            if watch_fallback is None:
                watch_fallback = SetupCandidate(
                    setup_id,
                    "WATCH",
                    direction,
                    predicates,
                    ("continuation_close_missing",),
                    trigger_bar_age=age,
                )
        if no_signal_fallback is None:
            reasons = tuple(predicate.name for predicate in predicates if not predicate.passed)
            no_signal_fallback = SetupCandidate(
                setup_id, "NO_SIGNAL", direction, predicates, reasons
            )
    if watch_fallback is not None:
        return watch_fallback
    if no_signal_fallback is not None:
        return no_signal_fallback
    reasons = tuple(
        predicate.name for predicate in context_predicates if not predicate.passed
    )
    return SetupCandidate(setup_id, "NO_SIGNAL", direction, context_predicates, reasons)


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
            level_style=candidate.level_style,
            trigger_bar_age=candidate.trigger_bar_age,
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
        level_style=candidate.level_style,
        trigger_bar_age=candidate.trigger_bar_age,
    )


def _mean_reversion_candidate(
    setup_id: str,
    primary: list[dict],
    *,
    z_entry: float = 1.5,
    eff_max: float = 0.35,
    lookback: int = 20,
    periods: SetupPeriods | None = None,
    series_cache: "SetupSeriesCache | None" = None,
    primary_tf: str | None = None,
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
    mean = None
    if (
        series_cache is not None
        and primary_tf is not None
        and series_cache.matches(primary_tf, primary)
    ):
        mean = series_cache.ema_at(primary_tf, len(primary), periods.ema_trend)
    if mean is None:
        mean = _ema_last(
            [float(candle["close"]) for candle in primary], periods.ema_trend
        )
    atr = _atr(primary, periods.atr, series_cache=series_cache, timeframe=primary_tf)
    efficiency_latest, _ = _efficiency_ratio(primary, lookback)
    if atr <= 0:
        return SetupCandidate(
            setup_id, "NO_SIGNAL", None,
            (_predicate("atr_valid", False, atr, "> 0"),),
            ("atr_invalid",), "mean_reversion",
        )
    watch_fallback: SetupCandidate | None = None
    no_signal_fallback: SetupCandidate | None = None
    # Trigger lookback: the stretch (z) and the reversal candle are judged on
    # each of the last N closed bars, newest first, with mean/ATR/efficiency
    # re-anchored to that bar so an older reversal is measured against the
    # regime it actually fired in.
    for age in _setup_trigger_offsets(len(primary)):
        scan = primary[: len(primary) - age]
        if age == 0:
            mean_at_bar = mean
            atr_at_bar = atr
            efficiency = efficiency_latest
        else:
            mean_at_bar = None
            if (
                series_cache is not None
                and primary_tf is not None
                and series_cache.matches(primary_tf, scan)
            ):
                mean_at_bar = series_cache.ema_at(primary_tf, len(scan), periods.ema_trend)
            if mean_at_bar is None:
                mean_at_bar = _ema_last(
                    [float(candle["close"]) for candle in scan], periods.ema_trend
                )
            atr_at_bar = _atr(scan, periods.atr)
            if atr_at_bar <= 0:
                continue
            efficiency, _ = _efficiency_ratio(scan, lookback)
        current = scan[-1]
        previous = scan[-2]
        close = float(current["close"])
        open_ = float(current["open"])
        prev_close = float(previous["close"])
        z = (close - mean_at_bar) / atr_at_bar
        ranging = efficiency <= eff_max
        direction_mr = "SHORT" if z >= z_entry else "LONG" if z <= -z_entry else None
        reversal = (
            (direction_mr == "SHORT" and close < open_ and close < prev_close)
            or (direction_mr == "LONG" and close > open_ and close > prev_close)
        )
        predicates = (
            _predicate("ranging_regime", ranging, round(efficiency, 4), f"efficiency <= {eff_max:.2f}"),
            _predicate("stretch_from_mean", direction_mr is not None, round(z, 4), f"|z| >= {z_entry:.2f}"),
            _predicate("reversal_confirmation", bool(reversal), close, "candle turns toward mean"),
        )
        if direction_mr is not None and ranging and reversal:
            return SetupCandidate(
                setup_id, "TRADE", direction_mr, predicates, (), "mean_reversion",
                trigger_bar_age=age,
            )
        if direction_mr is not None and ranging:
            if watch_fallback is None:
                watch_fallback = SetupCandidate(
                    setup_id, "WATCH", direction_mr, predicates,
                    ("reversal_confirmation_missing",), "mean_reversion",
                    trigger_bar_age=age,
                )
        if no_signal_fallback is None:
            reasons = tuple(predicate.name for predicate in predicates if not predicate.passed)
            no_signal_fallback = SetupCandidate(
                setup_id, "NO_SIGNAL", direction_mr, predicates, reasons, "mean_reversion"
            )
    if watch_fallback is not None:
        return watch_fallback
    if no_signal_fallback is not None:
        return no_signal_fallback
    return SetupCandidate(
        setup_id, "NO_SIGNAL", None,
        (_predicate("atr_valid", False, atr, "> 0"),),
        ("atr_invalid",), "mean_reversion",
    )


def _london_open_breakout_candidate(
    setup_id: str,
    primary: list[dict],
    *,
    display: str | None = None,
) -> SetupCandidate:
    """Asian range breakout during London open with tick-volume confirmation.

    Deliberately NOT part of the trigger lookback window: the 07:00-08:30 UTC
    entry window is 90 minutes wide, so a lookback of 3+ H1 bars reaches back
    before the window opens — a "stale" break here is out-of-session by
    definition and the session predicates already reject it.
    """
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
    structure_tf: str | None = None,
    series_cache: "SetupSeriesCache | None" = None,
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
    primary, context, primary_tf, context_tf = _resolve_setup_candle_frames(
        route,
        horizon,
        candles,
        entry_tf_override=entry_tf_override,
        structure_tf=structure_tf,
    )
    from factor_scoring import entry_tf_uses_period_overrides

    if entry_tf_uses_period_overrides(primary_tf):
        from engine_a_v3.profile import resolved_periods_for_tf

        periods = SetupPeriods.from_mapping(
            resolved_periods_for_tf(route.score_group, route.family, primary_tf)
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
                    series_cache=series_cache,
                    primary_tf=primary_tf,
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
                        series_cache=series_cache,
                        context_tf=context_tf,
                    ),
                    primary,
                    route,
                    display=display,
                ),
                _pullback_candidate(
                    setup_ids[-1],
                    primary,
                    context,
                    periods=periods,
                    series_cache=series_cache,
                    primary_tf=primary_tf,
                    context_tf=context_tf,
                    family=route.family,
                ),
            )
            if bool(CONFIG.get("ENGINE_A_V3_FOREX_MR_OVERLAY_ENABLED", False)):
                mr = CONFIG.get("ENGINE_A_V3_MEAN_REVERSION") or {}
                candidates = candidates + (
                    _mean_reversion_candidate(
                        "fx_range_mean_reversion",
                        primary,
                        z_entry=float(mr.get("Z_ENTRY", 1.5)),
                        eff_max=float(mr.get("EFF_MAX", 0.35)),
                        lookback=int(mr.get("LOOKBACK", 20)),
                        periods=periods,
                        series_cache=series_cache,
                        primary_tf=primary_tf,
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
                series_cache=series_cache,
                context_tf=context_tf,
            ),
            _pullback_candidate(
                setup_ids[-1],
                primary,
                context,
                periods=periods,
                series_cache=series_cache,
                primary_tf=primary_tf,
                context_tf=context_tf,
                family=route.family,
            ),
        )
    elif route.subclass in {"xau", "precious"}:
        if horizon == "intraday":
            candidates = (
                _with_session_gate(
                    _breakout_retest_candidate(
                        setup_ids[0],
                        primary,
                        context,
                        require_contraction=False,
                        lookback=24,
                        periods=periods,
                        series_cache=series_cache,
                        context_tf=context_tf,
                    ),
                    primary,
                    route,
                    display=display,
                ),
            )
            if intradaily_pullback_overlay_enabled(route.family, route.subclass):
                candidates = candidates + (
                    _with_session_gate(
                        _pullback_candidate(
                            "precious_trend_pullback",
                            primary,
                            context,
                            periods=periods,
                            series_cache=series_cache,
                            primary_tf=primary_tf,
                            context_tf=context_tf,
                            family=route.family,
                        ),
                        primary,
                        route,
                        display=display,
                    ),
                )
        else:
            candidates = (
                _pullback_candidate(
                    setup_ids[0],
                    primary,
                    context,
                    periods=periods,
                    series_cache=series_cache,
                    primary_tf=primary_tf,
                    context_tf=context_tf,
                    family=route.family,
                ),
            )
    elif route.family == "commodity":
        lookback, touch_distance, max_extension = _commodity_setup_params(route.subclass)
        if horizon == "intraday":
            candidates = (
                _breakout_retest_candidate(
                    setup_ids[0],
                    primary,
                    context,
                    require_contraction=False,
                    lookback=lookback,
                    periods=periods,
                    series_cache=series_cache,
                    context_tf=context_tf,
                ),
            )
            if intradaily_pullback_overlay_enabled(route.family, route.subclass):
                candidates = candidates + (
                    _pullback_candidate(
                        f"{route.subclass}_trend_pullback",
                        primary,
                        context,
                        touch_distance_atr=touch_distance,
                        max_extension_atr=max_extension,
                        periods=periods,
                        series_cache=series_cache,
                        primary_tf=primary_tf,
                        context_tf=context_tf,
                        family=route.family,
                    ),
                )
        else:
            candidates = (
                _pullback_candidate(
                    setup_ids[0],
                    primary,
                    context,
                    touch_distance_atr=touch_distance,
                    max_extension_atr=max_extension,
                    periods=periods,
                    series_cache=series_cache,
                    primary_tf=primary_tf,
                    context_tf=context_tf,
                    family=route.family,
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
                    opening_range_h1=candles.get("H1") or [],
                    periods=periods,
                    display=display,
                    series_cache=series_cache,
                    primary_tf=primary_tf,
                    context_tf=context_tf,
                ),
            )
            if intradaily_pullback_overlay_enabled(route.family, route.subclass):
                candidates = candidates + (
                    _pullback_candidate(
                        f"{route.subclass}_trend_pullback",
                        primary,
                        context,
                        periods=periods,
                        series_cache=series_cache,
                        primary_tf=primary_tf,
                        context_tf=context_tf,
                        family=route.family,
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
                        series_cache=series_cache,
                        context_tf=context_tf,
                    ),
                    _pullback_candidate(
                        setup_ids[0],
                        primary,
                        context,
                        periods=periods,
                        series_cache=series_cache,
                        primary_tf=primary_tf,
                        context_tf=context_tf,
                        family=route.family,
                    ),
                )
            )

    priority = {"TRADE": 2, "WATCH": 1, "NO_SIGNAL": 0}

    def _rank(candidate: SetupCandidate) -> tuple[int, int]:
        # Equal decisions tie-break on trigger freshness: a pattern that fired
        # on the newest bar outranks an equally-good older one (max() is
        # stable, so legacy first-listed order still wins true ties).
        age = candidate.trigger_bar_age
        return (priority[candidate.decision], -(age if age is not None else 10**6))

    return max(candidates, key=_rank)


def atr_for_levels(
    candles: list[dict],
    period: int = 14,
    *,
    series_cache: "SetupSeriesCache | None" = None,
    timeframe: str | None = None,
) -> float:
    """Wilder ATR for SL/TP geometry — matches indicators.calc_atr / quant bundle."""
    return _atr(candles, period, series_cache=series_cache, timeframe=timeframe)
