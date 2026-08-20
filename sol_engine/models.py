"""Typed value objects for the SOL analytical core."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import math
from typing import Any


TIMEFRAME_SECONDS: dict[str, int] = {
    "M1": 60,
    "M5": 5 * 60,
    "M15": 15 * 60,
    "M30": 30 * 60,
    "H1": 60 * 60,
    "H4": 4 * 60 * 60,
    "D1": 24 * 60 * 60,
}


def utc_iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class Candle:
    """One normalized OHLCV bar whose timestamp is the bar-open epoch."""

    time: float
    open: float
    high: float
    low: float
    close: float
    volume: float | None = None
    volume_source: str | None = None

    def __post_init__(self) -> None:
        values = (self.time, self.open, self.high, self.low, self.close)
        if not all(math.isfinite(float(value)) for value in values):
            raise ValueError("candle contains non-finite values")
        if self.time <= 0 or min(self.open, self.high, self.low, self.close) <= 0:
            raise ValueError("candle values must be positive")
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close):
            raise ValueError("candle OHLC envelope is malformed")
        if self.high < self.low:
            raise ValueError("candle high is below low")
        if self.volume is not None and (not math.isfinite(self.volume) or self.volume < 0):
            raise ValueError("candle volume must be finite and non-negative")

    @property
    def range(self) -> float:
        return self.high - self.low

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def upper_wick(self) -> float:
        return self.high - max(self.open, self.close)

    @property
    def lower_wick(self) -> float:
        return min(self.open, self.close) - self.low

    @property
    def typical(self) -> float:
        return (self.high + self.low + self.close) / 3.0

    def closes_at(self, timeframe: str) -> float:
        return self.time + TIMEFRAME_SECONDS[timeframe]

    def to_dict(self, timeframe: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "time": utc_iso(self.time),
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
            "volumeSource": self.volume_source,
        }
        if timeframe:
            payload["closedAt"] = utc_iso(self.closes_at(timeframe))
        return payload


@dataclass(slots=True)
class MarketSnapshot:
    pair: dict[str, Any]
    frames: dict[str, list[Candle]]
    provenance: dict[str, dict[str, Any]]
    as_of_epoch: float
    quality_errors: list[str] = field(default_factory=list)

    @property
    def display(self) -> str:
        return str(self.pair.get("display") or self.pair.get("symbol") or "UNKNOWN")

    @property
    def asset_type(self) -> str:
        return str(self.pair.get("type") or "unknown").strip().lower()

    @property
    def venue(self) -> str:
        return "bybit" if self.asset_type == "crypto" else "mt5"


@dataclass(frozen=True, slots=True)
class Quote:
    venue: str
    symbol: str
    bid: float
    ask: float
    timestamp: float
    source: str

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0

    @property
    def spread_bps(self) -> float:
        if self.mid <= 0:
            return math.inf
        return (self.ask - self.bid) / self.mid * 10_000.0

    def executable_price(self, direction: str) -> float:
        return self.ask if direction.upper() == "LONG" else self.bid

    def to_dict(self, *, now_epoch: float | None = None) -> dict[str, Any]:
        now_value = now_epoch if now_epoch is not None else datetime.now(timezone.utc).timestamp()
        return {
            "venue": self.venue,
            "symbol": self.symbol,
            "bid": self.bid,
            "ask": self.ask,
            "mid": self.mid,
            "spreadBps": self.spread_bps,
            "timestamp": utc_iso(self.timestamp),
            "ageSec": max(0.0, now_value - self.timestamp),
            "source": self.source,
        }

