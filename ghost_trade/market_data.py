"""Candle normalization primitives with explicit confirmed/forming separation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from statistics import fmean
from typing import Any, Callable, Mapping, Sequence

from .models import Candle


@dataclass(frozen=True, slots=True)
class CandleBundle:
    d1: tuple[Candle, ...]
    h4: tuple[Candle, ...]
    h1: tuple[Candle, ...]
    as_of: datetime
    m15_confirmed: tuple[Candle, ...] = ()
    m15_forming: Candle | None = None
    provider: str = ""


class CandleDataError(RuntimeError):
    pass


_TIMEFRAME_SECONDS = {"D1": 86400, "H4": 14400, "H1": 3600, "M15": 900}
_DEFAULT_LIMITS = {"D1": 300, "H4": 400, "H1": 500, "M15": 200}


def _timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        raw = float(value)
        if raw > 10_000_000_000:
            raw /= 1000.0
        result = datetime.fromtimestamp(raw, tz=timezone.utc)
    elif isinstance(value, str):
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise CandleDataError("invalid_candle_timestamp")
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def normalize_candle_rows(rows: Any, timeframe: str) -> tuple[Candle, ...]:
    if isinstance(rows, Mapping):
        if rows.get("error"):
            raise CandleDataError(str(rows.get("detail") or rows.get("error")))
        rows = rows.get("candles") or rows.get("data") or []
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        raise CandleDataError(f"invalid_candle_response:{timeframe}")
    seconds = _TIMEFRAME_SECONDS[timeframe]
    normalized: list[Candle] = []
    for row in rows:
        if isinstance(row, Candle):
            normalized.append(row)
            continue
        if not isinstance(row, Mapping):
            raise CandleDataError(f"invalid_candle_row:{timeframe}")
        opened = _timestamp(
            row.get("open_time", row.get("time", row.get("timestamp", row.get("t"))))
        )
        raw_close_time = row.get("close_time", row.get("closeTime"))
        closed = _timestamp(raw_close_time) if raw_close_time is not None else opened + timedelta(seconds=seconds)
        try:
            normalized.append(
                Candle(
                    open_time=opened,
                    close_time=closed,
                    open=float(row.get("open", row.get("o"))),
                    high=float(row.get("high", row.get("h"))),
                    low=float(row.get("low", row.get("l"))),
                    close=float(row.get("close", row.get("c"))),
                    volume=(
                        float(row.get("volume", row.get("v")))
                        if row.get("volume", row.get("v")) is not None
                        else None
                    ),
                )
            )
        except (TypeError, ValueError) as exc:
            raise CandleDataError(f"invalid_candle_ohlcv:{timeframe}") from exc
    normalized.sort(key=lambda item: item.open_time)
    return tuple(normalized)


class SharedCandleProvider:
    """Thin adapter around Athena's injected shared candle loader."""

    def __init__(
        self,
        loader: Callable[[Any, str, int], Any],
        *,
        limits: Mapping[str, int] | None = None,
    ):
        self._loader = loader
        self._limits = {**_DEFAULT_LIMITS, **dict(limits or {})}

    def load(self, instrument, style, as_of: datetime) -> CandleBundle:
        requested = ("D1", "H4", "H1", "M15") if str(style) == "intraday" else ("D1", "H4", "H1")
        loaded: dict[str, tuple[Candle, ...]] = {}
        for timeframe in requested:
            rows = self._loader(instrument, timeframe, int(self._limits[timeframe]))
            normalized = normalize_candle_rows(rows, timeframe)
            if not normalized:
                raise CandleDataError(f"no_candles:{timeframe}")
            loaded[timeframe] = normalized
        confirmed = {
            timeframe: confirmed_only(candles, as_of=as_of)
            for timeframe, candles in loaded.items()
        }
        m15_forming = None
        if "M15" in loaded:
            forming = [item for item in loaded["M15"] if item.close_time > as_of]
            m15_forming = forming[0] if forming else None
        return CandleBundle(
            d1=confirmed.get("D1", ()),
            h4=confirmed.get("H4", ()),
            h1=confirmed.get("H1", ()),
            m15_confirmed=confirmed.get("M15", ()),
            m15_forming=m15_forming,
            as_of=as_of,
            provider=instrument.venue.value,
        )


def confirmed_only(candles: Sequence[Candle], *, as_of: datetime) -> tuple[Candle, ...]:
    """Return only bars whose close timestamp existed at ``as_of``."""

    return tuple(candle for candle in candles if candle.close_time <= as_of)


def true_ranges(candles: Sequence[Candle]) -> list[float]:
    ranges: list[float] = []
    previous_close: float | None = None
    for candle in candles:
        if previous_close is None:
            value = candle.high - candle.low
        else:
            value = max(
                candle.high - candle.low,
                abs(candle.high - previous_close),
                abs(candle.low - previous_close),
            )
        ranges.append(max(0.0, float(value)))
        previous_close = candle.close
    return ranges


def atr_series(candles: Sequence[Candle], period: int = 14) -> list[float | None]:
    if period <= 0:
        raise ValueError("ATR period must be positive")
    ranges = true_ranges(candles)
    result: list[float | None] = [None] * len(ranges)
    for index in range(period - 1, len(ranges)):
        result[index] = fmean(ranges[index - period + 1 : index + 1])
    return result


def latest_atr(candles: Sequence[Candle], period: int = 14) -> float | None:
    series = atr_series(candles, period)
    return series[-1] if series else None
