"""Typed value objects for the MUSE analytical core."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import math
from typing import Any


CONTRACT_VERSION = "muse.v1"

TIMEFRAME_SECONDS: dict[str, int] = {
    "M1": 60,
    "M5": 5 * 60,
    "M15": 15 * 60,
    "M30": 30 * 60,
    "H1": 60 * 60,
    "H4": 4 * 60 * 60,
    "D1": 24 * 60 * 60,
}

# MUSE role ladder — deliberately distinct from FABLE (D1/H4/H1/M15) and
# GROK (D1/H1/M15/M5): atlas D1, current H4, vector M15, spark M5.
ROLE_TIMEFRAMES: dict[str, str] = {
    "atlas": "D1",
    "current": "H4",
    "vector": "M15",
    "spark": "M5",
}

DECISIONS: tuple[str, ...] = ("PRIME", "STAGE", "DORMANT", "BLOCKED")
SETUPS: tuple[str, ...] = ("TIDAL_SLING", "UNDERTOW_RECLAIM", "ARC_CONTINUATION", "HAVEN_TAP", "NONE")
PHASES: tuple[str, ...] = ("DRIFT", "PULL", "SURGE", "SETTLE", "RELEASE")
PRISM_NAMES: tuple[str, ...] = ("echo", "surge", "haven", "compass")


def utc_iso(epoch: float) -> str:
    return datetime.fromtimestamp(float(epoch), tz=timezone.utc).isoformat().replace("+00:00", "Z")


def parse_iso(value: Any) -> float:
    text = str(value or "").strip()
    if not text:
        raise ValueError("timestamp missing")
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


@dataclass(frozen=True, slots=True)
class Candle:
    """One normalized OHLCV bar whose timestamp is the bar-open epoch (UTC)."""

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
        if self.volume is not None and (not math.isfinite(self.volume) or self.volume < 0):
            raise ValueError("candle volume must be finite and non-negative")

    @property
    def range(self) -> float:
        return self.high - self.low

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def bullish(self) -> bool:
        return self.close > self.open

    @property
    def bearish(self) -> bool:
        return self.close < self.open

    def closes_at(self, timeframe: str) -> float:
        return self.time + TIMEFRAME_SECONDS[timeframe]


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
    def symbol(self) -> str:
        return str(self.pair.get("symbol") or self.pair.get("display") or "UNKNOWN")

    @property
    def asset_type(self) -> str:
        return str(self.pair.get("type") or "unknown").strip().lower()

    @property
    def venue(self) -> str:
        return "bybit" if self.asset_type == "crypto" else "mt5"

    def series(self, role: str) -> list[Candle]:
        return self.frames.get(ROLE_TIMEFRAMES[role], [])


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
        return self.ask if str(direction).upper() == "LONG" else self.bid

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


@dataclass(frozen=True, slots=True)
class Echo:
    """A sweep echo: excursion beyond a prior extreme + fast reclaim."""

    direction: str  # post-echo trade direction
    extreme: float
    base: float  # reclaimed level
    depth_atr: float
    reclaim_bars: int
    velocity: float  # depth_atr / max(1, reclaim_bars)
    bars_since: int


@dataclass(frozen=True, slots=True)
class Haven:
    """One unfilled imbalance cell in the haven lattice."""

    kind: str  # "void" | "base"
    low: float
    high: float
    index: int
    time: float
    age_bars: int

    @property
    def mid(self) -> float:
        return (self.low + self.high) / 2.0
