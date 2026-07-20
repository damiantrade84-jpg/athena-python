from __future__ import annotations

import bisect
import math
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any, Mapping

import pandas as pd

from .constants import TIMEFRAME_SECONDS

class CausalMarketView:
    """Indexed immutable bar store with close-time point-in-time admission."""

    def __init__(self, frames: Mapping[str, pd.DataFrame]):
        self.frames: dict[str, pd.DataFrame] = {}
        self.rows: dict[str, list[dict[str, Any]]] = {}
        self.open_ns: dict[str, list[int]] = {}
        self.close_ns: dict[str, list[int]] = {}
        for timeframe, source in frames.items():
            tf = str(timeframe).upper()
            if tf not in TIMEFRAME_SECONDS:
                continue
            frame = source.copy()
            frame["time"] = pd.to_datetime(frame["time"], utc=True)
            frame = frame.sort_values("time", kind="mergesort").reset_index(drop=True)
            self.frames[tf] = frame
            timestamps = frame["time"].tolist()
            volumes = (
                frame["volume"].tolist()
                if "volume" in frame.columns
                else frame["vol"].tolist()
                if "vol" in frame.columns
                else [0.0] * len(frame)
            )
            self.rows[tf] = [
                {
                    "time": timestamp.isoformat(),
                    "open": float(open_),
                    "high": float(high),
                    "low": float(low),
                    "close": float(close),
                    "volume": float(volume or 0.0),
                    "vol": float(volume or 0.0),
                }
                for timestamp, open_, high, low, close, volume in zip(
                    timestamps,
                    frame["open"].tolist(),
                    frame["high"].tolist(),
                    frame["low"].tolist(),
                    frame["close"].tolist(),
                    volumes,
                )
            ]
            opens = frame["time"].array.asi8.tolist()
            self.open_ns[tf] = opens
            duration_ns = TIMEFRAME_SECONDS[tf] * 1_000_000_000
            self.close_ns[tf] = [value + duration_ns for value in opens]

    def decision_times(self, execution_timeframe: str) -> list[datetime]:
        tf = str(execution_timeframe).upper()
        return [
            datetime.fromtimestamp(value / 1_000_000_000, tz=timezone.utc)
            for value in self.close_ns.get(tf, [])
        ]

    def iter_confirmed_snapshots(
        self,
        execution_timeframe: str,
    ) -> Iterator[tuple[datetime, dict[str, list[dict[str, Any]]]]]:
        """Advance confirmed prefixes in place without quadratic list copies.

        The yielded lists are mutated only after the caller advances the
        iterator. Consumers must evaluate and serialize each decision before
        requesting the next snapshot, which is exactly how replay runs.
        """

        buffers: dict[str, list[dict[str, Any]]] = {
            timeframe: [] for timeframe in self.rows
        }
        lengths = {timeframe: 0 for timeframe in self.rows}
        for decision_time in self.decision_times(execution_timeframe):
            for timeframe, rows in self.rows.items():
                length = self.confirmed_length(timeframe, decision_time)
                previous = lengths[timeframe]
                if length > previous:
                    buffers[timeframe].extend(rows[previous:length])
                    lengths[timeframe] = length
            yield decision_time, buffers

    def confirmed_length(self, timeframe: str, decision_time: datetime) -> int:
        tf = str(timeframe).upper()
        timestamp_ns = int(pd.Timestamp(decision_time).value)
        return bisect.bisect_right(self.close_ns.get(tf, []), timestamp_ns)

    def confirmed(self, timeframe: str, decision_time: datetime) -> list[dict[str, Any]]:
        tf = str(timeframe).upper()
        return self.rows.get(tf, [])[: self.confirmed_length(tf, decision_time)]

    def _forming_from_lower(self, timeframe: str, decision_time: datetime) -> dict[str, Any] | None:
        tf = str(timeframe).upper()
        target_seconds = TIMEFRAME_SECONDS[tf]
        target_opens = self.open_ns.get(tf, [])
        if not target_opens:
            return None
        decision_ns = int(pd.Timestamp(decision_time).value)
        target_index = bisect.bisect_right(target_opens, decision_ns) - 1
        if target_index < 0:
            return None
        target_open_ns = target_opens[target_index]
        target_close_ns = target_open_ns + target_seconds * 1_000_000_000
        if decision_ns > target_close_ns:
            return None
        lower_candidates = sorted(
            (
                (TIMEFRAME_SECONDS[candidate], candidate)
                for candidate in self.rows
                if TIMEFRAME_SECONDS[candidate] < target_seconds
            ),
            reverse=True,
        )
        for _seconds, lower_tf in lower_candidates:
            lower_opens = self.open_ns[lower_tf]
            start = bisect.bisect_left(lower_opens, target_open_ns)
            end = bisect.bisect_right(self.close_ns[lower_tf], decision_ns)
            subset = self.rows[lower_tf][start:end]
            if not subset:
                continue
            return {
                "time": datetime.fromtimestamp(target_open_ns / 1_000_000_000, tz=timezone.utc).isoformat(),
                "open": float(subset[0]["open"]),
                "high": max(float(row["high"]) for row in subset),
                "low": min(float(row["low"]) for row in subset),
                "close": float(subset[-1]["close"]),
                "volume": sum(float(row.get("volume", row.get("vol", 0.0)) or 0.0) for row in subset),
                "vol": sum(float(row.get("volume", row.get("vol", 0.0)) or 0.0) for row in subset),
                "is_forming": True,
            }
        if decision_ns == target_close_ns:
            row = dict(self.rows[tf][target_index])
            row["is_forming"] = True
            return row
        return None

    def snapshot(
        self,
        decision_time: datetime,
        *,
        forming_timeframes: set[str] | None = None,
        limits: Mapping[str, int] | None = None,
    ) -> tuple[dict[str, list[dict[str, Any]]], set[str]]:
        requested_forming = {str(tf).upper() for tf in (forming_timeframes or set())}
        resolved_limits: dict[str, int] = {}
        for raw_timeframe, raw_limit in (limits or {}).items():
            try:
                resolved_limits[str(raw_timeframe).upper()] = max(1, int(raw_limit))
            except (TypeError, ValueError):
                continue
        result: dict[str, list[dict[str, Any]]] = {}
        actual_forming: set[str] = set()
        decision_ns = int(pd.Timestamp(decision_time).value)
        for timeframe in self.rows:
            if timeframe in requested_forming:
                confirmed_length = bisect.bisect_left(self.close_ns.get(timeframe, []), decision_ns)
                forming = self._forming_from_lower(timeframe, decision_time)
                limit = resolved_limits.get(timeframe)
                confirmed_capacity = (
                    max(0, limit - (1 if forming is not None else 0))
                    if limit is not None
                    else confirmed_length
                )
                start = max(0, confirmed_length - confirmed_capacity)
                confirmed = self.rows.get(timeframe, [])[start:confirmed_length]
                if forming is not None:
                    confirmed = confirmed + [forming]
                    actual_forming.add(timeframe)
            else:
                confirmed_length = bisect.bisect_right(self.close_ns.get(timeframe, []), decision_ns)
                limit = resolved_limits.get(timeframe)
                start = max(0, confirmed_length - limit) if limit is not None else 0
                confirmed = self.rows.get(timeframe, [])[start:confirmed_length]
            result[timeframe] = confirmed
        return result, actual_forming


def _previous_valid(series: list[Any], index: int) -> Any:
    for position in range(index, -1, -1):
        if series[position] is not None:
            return series[position]
    return None


def _tail(series: list[Any], index: int, limit: int) -> list[float]:
    values: list[float] = []
    for position in range(index, -1, -1):
        value = series[position]
        if value is not None:
            values.append(float(value))
        if len(values) >= limit:
            break
    values.reverse()
    return values


def _legacy_percentile(series: list[Any], index: int, lookback: int) -> tuple[float | None, str]:
    values = _tail(series, index, lookback)
    if len(values) < 20:
        return None, "insufficient data"
    current = values[-1]
    percentile = round(sum(1 for value in values if value <= current) / len(values) * 100, 1)
    return percentile, "expanding" if len(values) >= 3 and values[-1] > values[-2] else "contracting"


def _adx_momentum(series: list[Any], index: int, window: int = 5) -> tuple[float, str]:
    values = _tail(series, index, 20)
    if len(values) < window + 2:
        return 0.0, "stable"
    recent = values[-(window + 1) :]
    slope = (recent[-1] - recent[0]) / window
    peak = max(values)
    current = values[-1]
    if slope > 0.5:
        return round(slope, 2), "strengthening"
    if peak > 30 and current < peak * 0.8 and slope < -0.3:
        return round(slope, 2), "collapsing"
    if peak > 25 and slope < -0.3:
        return round(slope, 2), "exhausting"
    return round(slope, 2), "stable"


def _rolling_legacy_percentiles(
    series: list[Any], lookback: int
) -> tuple[list[float | None], list[str]] | None:
    """Precompute the exact latest-value contract of `_legacy_percentile`."""

    window: list[float] = []
    ordered: list[float] = []
    percentiles: list[float | None] = []
    labels: list[str] = []
    for raw in series:
        if raw is not None:
            try:
                value = float(raw)
            except (TypeError, ValueError):
                return None
            if not math.isfinite(value):
                return None
            window.append(value)
            bisect.insort_right(ordered, value)
            if len(window) > lookback:
                removed = window.pop(0)
                ordered.pop(bisect.bisect_left(ordered, removed))
        if len(window) < 20:
            percentiles.append(None)
            labels.append("insufficient data")
            continue
        current = window[-1]
        percentiles.append(
            round(bisect.bisect_right(ordered, current) / len(window) * 100, 1)
        )
        labels.append(
            "expanding" if len(window) >= 3 and window[-1] > window[-2] else "contracting"
        )
    return percentiles, labels


def _rolling_adx_momentum(
    series: list[Any], window_size: int = 5
) -> tuple[list[float], list[str]] | None:
    values: list[float] = []
    slopes: list[float] = []
    states: list[str] = []
    for raw in series:
        if raw is not None:
            try:
                value = float(raw)
            except (TypeError, ValueError):
                return None
            if not math.isfinite(value):
                return None
            values.append(value)
            if len(values) > 20:
                values.pop(0)
        if len(values) < window_size + 2:
            slopes.append(0.0)
            states.append("stable")
            continue
        recent = values[-(window_size + 1):]
        slope = (recent[-1] - recent[0]) / window_size
        peak = max(values)
        current = values[-1]
        if slope > 0.5:
            state = "strengthening"
        elif peak > 30 and current < peak * 0.8 and slope < -0.3:
            state = "collapsing"
        elif peak > 25 and slope < -0.3:
            state = "exhausting"
        else:
            state = "stable"
        slopes.append(round(slope, 2))
        states.append(state)
    return slopes, states


def _previous_valid_series(series: list[Any]) -> list[Any]:
    previous: Any = None
    result: list[Any] = []
    for value in series:
        result.append(previous)
        if value is not None:
            previous = value
    return result


class CausalIndicatorIndex(dict):
    """Fresh v2 O(1) point-in-time indicator snapshot index.

    This deliberately does not import the retired backtest cache.  It computes
    production indicator arrays once from immutable series and reduces only
    causal values at the requested prefix.
    """

    def __init__(self, candles: Mapping[str, list[dict[str, Any]]]):
        super().__init__()
        self._candles = candles
        self._series: dict[tuple[Any, ...], dict[str, Any] | None] = {}
        self._forming_lengths: set[tuple[str, int]] = set()
        self._latest_snapshot_key: dict[str, tuple[Any, ...]] = {}

    def __setitem__(self, key: Any, value: Any) -> None:
        # Quant scoring only revisits the latest length for each timeframe.
        # Retaining every historical snapshot grows linearly with every
        # decision timestamp and provides no cache hit on monotonic replay.
        if isinstance(key, tuple) and len(key) == 2:
            timeframe = str(key[0]).upper()
            previous = self._latest_snapshot_key.get(timeframe)
            if previous is not None and previous != key:
                super().pop(previous, None)
            self._latest_snapshot_key[timeframe] = key
        super().__setitem__(key, value)

    def set_forming_lengths(self, values: set[tuple[str, int]]) -> None:
        self._forming_lengths = {(str(tf).upper(), int(length)) for tf, length in values}
        for key in self._forming_lengths:
            super().pop(key, None)

    def snapshot_at(
        self,
        timeframe: str,
        length: int,
        periods: Mapping[str, int],
        asset_type: str,
    ) -> dict[str, Any] | None:
        rows = self._candles.get(timeframe) or []
        if (str(timeframe).upper(), int(length)) in self._forming_lengths:
            return None
        if length <= 0 or length > len(rows):
            return None
        try:
            period_key = tuple(sorted((str(key), int(value)) for key, value in periods.items()))
        except (TypeError, ValueError):
            return None
        key = (str(timeframe), period_key, str(asset_type or ""))
        if key not in self._series:
            try:
                from indicators import _calc_indicator_bundle, get_normalization_lookback, percentile_rank

                bundle = _calc_indicator_bundle(rows, periods=dict(periods))
                lookback = get_normalization_lookback(asset_type)
                legacy_adx = _rolling_legacy_percentiles(bundle["adx"]["adx"], 252)
                legacy_atr = _rolling_legacy_percentiles(bundle["atr"], 100)
                adx_momentum = _rolling_adx_momentum(bundle["adx"]["adx"])
                self._series[key] = {
                    "bundle": bundle,
                    "adx_percentiles": percentile_rank(bundle["adx"]["adx"], lookback),
                    "atr_percentiles": percentile_rank(bundle["atr"], lookback),
                    "legacy_adx": legacy_adx,
                    "legacy_atr": legacy_atr,
                    "adx_momentum": adx_momentum,
                    "rsi_previous": _previous_valid_series(bundle["rsi"]),
                    "adx_previous": _previous_valid_series(bundle["adx"]["adx"]),
                }
            except (KeyError, TypeError, ValueError, ZeroDivisionError):
                self._series[key] = None
        data = self._series[key]
        if data is None:
            return None
        latest = length - 1
        bundle = data["bundle"]
        closes = bundle["close"]
        if latest >= len(closes):
            return None
        ema21 = bundle["ema21"]
        ema50 = bundle["ema50"]
        ema200 = bundle["ema200"]
        rsi = bundle["rsi"]
        macd = bundle["macd"]
        atr = bundle["atr"]
        adx = bundle["adx"]
        bb = bundle["bb"]
        legacy_adx = data["legacy_adx"]
        legacy_atr = data["legacy_atr"]
        adx_momentum = data["adx_momentum"]
        if legacy_adx is None:
            adx_percentile, adx_label = _legacy_percentile(adx["adx"], latest, 252)
        else:
            adx_percentile = legacy_adx[0][latest]
            adx_label = legacy_adx[1][latest]
        if legacy_atr is None:
            atr_percentile, atr_label = _legacy_percentile(atr, latest, 100)
        else:
            atr_percentile = legacy_atr[0][latest]
            atr_label = legacy_atr[1][latest]
        if adx_momentum is None:
            adx_slope, adx_state = _adx_momentum(adx["adx"], latest)
        else:
            adx_slope = adx_momentum[0][latest]
            adx_state = adx_momentum[1][latest]
        ema_slope = (
            round((ema200[latest] - ema200[latest - 10]) / ema200[latest - 10] * 100, 3)
            if latest >= 10 and ema200[latest] and ema200[latest - 10]
            else 0
        )
        return {
            "ema21": ema21[latest], "ema50": ema50[latest], "ema200": ema200[latest],
            "close": closes[latest], "rsi": rsi[latest],
            "rsiPrev": data["rsi_previous"][latest],
            "macdLine": macd["macd"][latest], "macdSignal": macd["signal"][latest],
            "macdHist": macd["hist"][latest],
            "macdHistPrev": macd["hist"][latest - 1] if latest > 0 else None,
            "atr": atr[latest], "adx": adx["adx"][latest],
            "adxPrev": data["adx_previous"][latest],
            "adxPct": adx_percentile, "adxLabel": adx_label,
            "atrPct": atr_percentile, "atrLabel": atr_label,
            "adxSlope": adx_slope, "adxMomentum": adx_state,
            "plusDI": adx["plusDI"][latest], "minusDI": adx["minusDI"][latest],
            "bbUpper": bb["upper"][latest], "bbMid": bb["mid"][latest],
            "bbLower": bb["lower"][latest], "ema200Slope10": ema_slope,
            "adx_pct": data["adx_percentiles"][latest],
            "atr_pct": data["atr_percentiles"][latest],
        }
