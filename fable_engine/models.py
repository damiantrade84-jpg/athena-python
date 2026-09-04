"""Typed value objects for the FABLE analytical core."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import math
from typing import Any


CONTRACT_VERSION = "fable.v1"

TIMEFRAME_SECONDS: dict[str, int] = {
    "M1": 60,
    "M5": 5 * 60,
    "M15": 15 * 60,
    "M30": 30 * 60,
    "H1": 60 * 60,
    "H4": 4 * 60 * 60,
    "D1": 24 * 60 * 60,
}

# Role ladder owned by FABLE. Roles are distinct from every other engine:
#   draw      D1  — dealing range, premium/discount, external liquidity targets
#   bias      H4  — swing sequence bias and trend efficiency
#   pools     H1  — intraday liquidity pools (swing highs/lows, equal levels)
#   narrative M15 — raid, shift, imbalance and return (entry timeframe)
ROLE_TIMEFRAMES: dict[str, str] = {
    "draw": "D1",
    "bias": "H4",
    "pools": "H1",
    "narrative": "M15",
}

DECISIONS: tuple[str, ...] = ("EXECUTE", "STAGE", "OBSERVE", "VOID")
TIERS: tuple[str, ...] = ("LEGEND", "SAGA", "TALE", "SKETCH")
ACT_NAMES: tuple[str, ...] = ("draw", "raid", "shift", "return", "chorus")


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

    @property
    def upper_wick(self) -> float:
        return self.high - max(self.open, self.close)

    @property
    def lower_wick(self) -> float:
        return min(self.open, self.close) - self.low

    def closes_at(self, timeframe: str) -> float:
        return self.time + TIMEFRAME_SECONDS[timeframe]

    def to_dict(self) -> dict[str, Any]:
        return {
            "time": int(self.time),
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
        }


@dataclass(slots=True)
class MarketSnapshot:
    """Closed-bar series for every FABLE role plus provenance and quality errors."""

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
class Swing:
    """A fractal pivot on one series."""

    index: int
    time: float
    price: float
    kind: str  # "high" | "low"


@dataclass(frozen=True, slots=True)
class LiquidityPool:
    """A resting-liquidity level built from swings, equal levels or session extremes."""

    price: float
    side: str  # "buyside" (above price, stops of shorts) | "sellside" (below price)
    source: str  # e.g. "H1_swing", "H4_swing", "PDH", "PDL", "PWH", "PWL", "EQH", "EQL"
    strength: float  # 0..1
    time: float  # epoch of the bar that formed the level
    touches: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "price": self.price,
            "side": self.side,
            "source": self.source,
            "strength": round(self.strength, 4),
            "time": int(self.time),
            "touches": self.touches,
        }


@dataclass(frozen=True, slots=True)
class Raid:
    """A liquidity sweep: an excursion through a pool that closed back inside."""

    pool: LiquidityPool
    direction: str  # narrative direction after the raid: LONG after a sellside raid
    start_index: int
    reclaim_index: int
    extreme: float
    depth_atr: float
    reclaim_atr: float
    bars_since: int
    participation_z: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "pool": self.pool.to_dict(),
            "direction": self.direction,
            "startIndex": self.start_index,
            "reclaimIndex": self.reclaim_index,
            "extreme": self.extreme,
            "depthAtr": round(self.depth_atr, 4),
            "reclaimAtr": round(self.reclaim_atr, 4),
            "barsSince": self.bars_since,
            "participationZ": None if self.participation_z is None else round(self.participation_z, 3),
        }


@dataclass(frozen=True, slots=True)
class Imbalance:
    """A fair value gap or order block left behind by a displacement leg."""

    kind: str  # "fvg" | "order_block"
    low: float
    high: float
    index: int
    time: float

    @property
    def mid(self) -> float:
        return (self.low + self.high) / 2.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "low": self.low,
            "high": self.high,
            "mid": self.mid,
            "index": self.index,
            "time": int(self.time),
        }


@dataclass(frozen=True, slots=True)
class Shift:
    """A market-structure shift driven by displacement after a raid."""

    direction: str
    broken_level: float
    broken_swing_index: int
    break_index: int
    leg_start: float  # raid extreme
    leg_end: float  # displacement extreme
    leg_end_index: int
    displacement_atr: float  # net leg travel in ATR
    max_body_atr: float  # largest single-bar body in ATR inside the leg
    imbalances: tuple[Imbalance, ...]
    participation_z: float | None
    bars_since_break: int = 0  # closed bars between the structure break and the evaluation bar

    @property
    def leg_range(self) -> float:
        return abs(self.leg_end - self.leg_start)

    def to_dict(self) -> dict[str, Any]:
        return {
            "direction": self.direction,
            "brokenLevel": self.broken_level,
            "brokenSwingIndex": self.broken_swing_index,
            "breakIndex": self.break_index,
            "legStart": self.leg_start,
            "legEnd": self.leg_end,
            "legEndIndex": self.leg_end_index,
            "displacementAtr": round(self.displacement_atr, 4),
            "maxBodyAtr": round(self.max_body_atr, 4),
            "imbalances": [item.to_dict() for item in self.imbalances],
            "participationZ": None if self.participation_z is None else round(self.participation_z, 3),
            "barsSinceBreak": self.bars_since_break,
        }
