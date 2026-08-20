"""Core value types for OPUS.

Deliberately plain dataclasses: the engine core is pure and testable without a
broker, a database or a network.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import numpy as np


class Direction(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"

    @property
    def sign(self) -> int:
        return 1 if self is Direction.LONG else -1

    @classmethod
    def from_sign(cls, value: float) -> "Direction":
        return cls.LONG if value >= 0 else cls.SHORT


class Archetype(str, Enum):
    """The four setups OPUS is allowed to express."""

    SWEEP_RECLAIM = "SWEEP_RECLAIM"
    DISPLACEMENT_CONTINUATION = "DISPLACEMENT_CONTINUATION"
    VALUE_FADE = "VALUE_FADE"
    VACUUM_BREAK = "VACUUM_BREAK"


class Regime(str, Enum):
    """Volatility x balance quadrant."""

    TREND_DRIVE = "TREND_DRIVE"        # imbalanced + expanding
    COILED = "COILED"                  # imbalanced + contracting
    VOLATILE_RANGE = "VOLATILE_RANGE"  # balanced + expanding
    DORMANT = "DORMANT"                # balanced + contracting


class Decision(str, Enum):
    TRADE = "TRADE"
    WATCH = "WATCH"
    STAND_DOWN = "STAND_DOWN"


class Readiness(str, Enum):
    """Separate from `Decision`. A TRADE decision is not executable proof."""

    READY = "READY"      # every gate green, live quote valid, place now
    ARMED = "ARMED"      # thesis valid, waiting for price to reach entry
    BLOCKED = "BLOCKED"  # a hard gate failed


@dataclass(frozen=True)
class Candles:
    """Immutable OHLCV block for one symbol/timeframe. Confirmed bars only.

    `ts` is epoch seconds of the bar OPEN, ascending, no duplicates.
    """

    symbol: str
    timeframe: str
    ts: np.ndarray
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    volume: np.ndarray
    source: str = "unknown"
    volume_is_tick_count: bool = False

    def __len__(self) -> int:
        return int(self.close.shape[0])

    def __post_init__(self) -> None:
        n = len(self.close)
        for name in ("ts", "open", "high", "low", "volume"):
            arr = getattr(self, name)
            if arr.shape[0] != n:
                raise ValueError(
                    f"{self.symbol} {self.timeframe}: {name} length "
                    f"{arr.shape[0]} != close length {n}"
                )

    def tail(self, n: int) -> "Candles":
        if n >= len(self):
            return self
        return Candles(
            symbol=self.symbol,
            timeframe=self.timeframe,
            ts=self.ts[-n:],
            open=self.open[-n:],
            high=self.high[-n:],
            low=self.low[-n:],
            close=self.close[-n:],
            volume=self.volume[-n:],
            source=self.source,
            volume_is_tick_count=self.volume_is_tick_count,
        )

    @property
    def last_ts(self) -> float:
        return float(self.ts[-1]) if len(self) else 0.0


@dataclass(frozen=True)
class Quote:
    """Executable live quote. OPUS never prices an entry off a candle close."""

    symbol: str
    bid: float
    ask: float
    ts: float
    source: str = "unknown"

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0

    @property
    def spread(self) -> float:
        return max(0.0, self.ask - self.bid)

    @property
    def spread_pct(self) -> float:
        m = self.mid
        return (self.spread / m * 100.0) if m > 0 else float("inf")

    def age_sec(self, now: float) -> float:
        return max(0.0, now - self.ts)

    def entry_price(self, direction: Direction) -> float:
        """Cross the spread: buy the ask, sell the bid."""
        return self.ask if direction is Direction.LONG else self.bid


@dataclass
class GateResult:
    """One deterministic check. Hard failures are non-negotiable."""

    name: str
    passed: bool
    hard: bool = True
    detail: str = ""
    observed: Any = None
    limit: Any = None

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "passed": bool(self.passed),
            "hard": bool(self.hard),
            "detail": self.detail,
            "observed": plain(self.observed),
            "limit": plain(self.limit),
        }


@dataclass
class IndicatorReading:
    """A single OPUS indicator's output.

    `value` is always signed and squashed into [-1, 1] where positive means
    "supports long". `confidence` in [0, 1] reports how much data actually
    backed the reading, so thin evidence is down-weighted rather than trusted.
    """

    name: str
    value: float
    confidence: float = 1.0
    detail: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "value": rnd(self.value),
            "confidence": rnd(self.confidence),
            "detail": {k: plain(v) for k, v in self.detail.items()},
        }


@dataclass
class Levels:
    """Structural trade geometry, derived from price structure rather than from
    a fixed volatility multiple alone."""

    entry: float
    stop: float
    targets: list[float] = field(default_factory=list)
    entry_kind: str = "limit"  # limit | market | stop
    stop_basis: str = ""       # which structure defined the stop
    target_basis: list[str] = field(default_factory=list)

    @property
    def risk_per_unit(self) -> float:
        return abs(self.entry - self.stop)

    def reward_multiple(self, index: int = 0) -> float:
        r = self.risk_per_unit
        if r <= 0 or index >= len(self.targets):
            return 0.0
        return abs(self.targets[index] - self.entry) / r

    def as_dict(self) -> dict:
        return {
            "entry": rnd(self.entry, 8),
            "stop": rnd(self.stop, 8),
            "targets": [rnd(t, 8) for t in self.targets],
            "entryKind": self.entry_kind,
            "stopBasis": self.stop_basis,
            "targetBasis": list(self.target_basis),
            "riskPerUnit": rnd(self.risk_per_unit, 8),
        }


@dataclass
class Signal:
    """A fully-formed OPUS signal."""

    symbol: str
    display: str
    asset_class: str
    direction: Direction
    archetype: Archetype
    regime: Regime
    decision: Decision
    readiness: Readiness

    conviction: float     # [0, 1] strength aligned to `direction`
    coherence: float      # [0, 1] agreement across indicators
    probability: float    # calibrated P(target before stop)
    expectancy_r: float   # net expected R after costs
    cost_r: float         # round-trip cost expressed in R

    levels: Levels
    readings: list[IndicatorReading] = field(default_factory=list)
    gates: list[GateResult] = field(default_factory=list)

    size_units: float = 0.0
    risk_pct: float = 0.0
    kelly_fraction: float = 0.0

    quote: Quote | None = None
    timeframes: dict = field(default_factory=dict)
    provenance: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    created_ts: float = 0.0
    signal_id: str = ""

    @property
    def blocking_gates(self) -> list[GateResult]:
        return [g for g in self.gates if g.hard and not g.passed]

    @property
    def rr(self) -> float:
        return self.levels.reward_multiple(0)

    def as_dict(self) -> dict:
        return {
            "signalId": self.signal_id,
            "symbol": self.symbol,
            "display": self.display,
            "assetClass": self.asset_class,
            "direction": self.direction.value,
            "archetype": self.archetype.value,
            "regime": self.regime.value,
            "decision": self.decision.value,
            "readiness": self.readiness.value,
            "conviction": rnd(self.conviction),
            "convictionScore": rnd(self.conviction * 100.0, 2),
            "coherence": rnd(self.coherence),
            "probability": rnd(self.probability),
            "expectancyR": rnd(self.expectancy_r, 4),
            "costR": rnd(self.cost_r, 4),
            "rr": rnd(self.rr, 3),
            "levels": self.levels.as_dict(),
            "readings": [r.as_dict() for r in self.readings],
            "gates": [g.as_dict() for g in self.gates],
            "blockingGates": [g.name for g in self.blocking_gates],
            "sizeUnits": rnd(self.size_units, 6),
            "riskPct": rnd(self.risk_pct, 4),
            "kellyFraction": rnd(self.kelly_fraction, 4),
            "quote": (
                {
                    "bid": rnd(self.quote.bid, 8),
                    "ask": rnd(self.quote.ask, 8),
                    "spreadPct": rnd(self.quote.spread_pct, 5),
                    "ts": self.quote.ts,
                    "source": self.quote.source,
                }
                if self.quote
                else None
            ),
            "timeframes": plain(self.timeframes),
            "provenance": plain(self.provenance),
            "notes": list(self.notes),
            "createdTs": self.created_ts,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "Signal":
        """Rehydrate a signal that this engine previously serialised.

        Used by execute so a TRADE card can be submitted from the store when
        a fresh rescore no longer emits the same signal_id. Missing required
        fields raise rather than inventing geometry or readiness.
        """
        if not isinstance(payload, dict):
            raise ValueError("signal payload is not an object")
        signal_id = str(payload.get("signalId") or "").strip()
        symbol = str(payload.get("symbol") or "").strip()
        if not signal_id or not symbol:
            raise ValueError("signal payload missing signalId or symbol")

        levels_raw = payload.get("levels")
        if not isinstance(levels_raw, dict):
            raise ValueError("signal payload missing levels")
        try:
            entry = float(levels_raw["entry"])
            stop = float(levels_raw["stop"])
            targets = [float(t) for t in (levels_raw.get("targets") or [])]
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("signal payload has unusable levels") from exc
        if not targets:
            raise ValueError("signal payload missing targets")
        if not (
            np.isfinite(entry) and np.isfinite(stop)
            and all(np.isfinite(t) for t in targets)
        ):
            raise ValueError("signal payload has non-finite levels")

        quote = None
        quote_raw = payload.get("quote")
        if isinstance(quote_raw, dict):
            try:
                quote = Quote(
                    symbol=symbol,
                    bid=float(quote_raw["bid"]),
                    ask=float(quote_raw["ask"]),
                    ts=float(quote_raw.get("ts") or 0.0),
                    source=str(quote_raw.get("source") or "unknown"),
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError("signal payload has unusable quote") from exc

        gates_raw = payload.get("gates")
        if not isinstance(gates_raw, list) or not gates_raw:
            raise ValueError("signal payload missing gates")
        gates: list[GateResult] = []
        for raw in gates_raw:
            if not isinstance(raw, dict):
                raise ValueError("signal payload has malformed gates")
            gates.append(GateResult(
                name=str(raw.get("name") or ""),
                passed=bool(raw.get("passed")),
                hard=bool(raw.get("hard", True)),
                detail=str(raw.get("detail") or ""),
                observed=raw.get("observed"),
                limit=raw.get("limit"),
            ))

        readings: list[IndicatorReading] = []
        for raw in payload.get("readings") or []:
            if not isinstance(raw, dict):
                continue
            detail = raw.get("detail")
            readings.append(IndicatorReading(
                name=str(raw.get("name") or ""),
                value=float(raw.get("value") or 0.0),
                confidence=float(raw.get("confidence") or 0.0),
                detail=dict(detail) if isinstance(detail, dict) else {},
            ))

        try:
            direction = Direction(str(payload["direction"]))
            archetype = Archetype(str(payload["archetype"]))
            decision = Decision(str(payload["decision"]))
            readiness = Readiness(str(payload["readiness"]))
        except (KeyError, ValueError) as exc:
            raise ValueError("signal payload missing decision fields") from exc

        regime_raw = payload.get("regime") or Regime.DORMANT.value
        try:
            regime = Regime(str(regime_raw))
        except ValueError as exc:
            raise ValueError("signal payload has unknown regime") from exc

        return cls(
            symbol=symbol,
            display=str(payload.get("display") or symbol),
            asset_class=str(payload.get("assetClass") or ""),
            direction=direction,
            archetype=archetype,
            regime=regime,
            decision=decision,
            readiness=readiness,
            conviction=float(payload.get("conviction") or 0.0),
            coherence=float(payload.get("coherence") or 0.0),
            probability=float(payload.get("probability") or 0.0),
            expectancy_r=float(payload.get("expectancyR") or 0.0),
            cost_r=float(payload.get("costR") or 0.0),
            levels=Levels(
                entry=entry,
                stop=stop,
                targets=targets,
                entry_kind=str(levels_raw.get("entryKind") or "limit"),
                stop_basis=str(levels_raw.get("stopBasis") or ""),
                target_basis=[str(b) for b in (levels_raw.get("targetBasis") or [])],
            ),
            readings=readings,
            gates=gates,
            size_units=float(payload.get("sizeUnits") or 0.0),
            risk_pct=float(payload.get("riskPct") or 0.0),
            kelly_fraction=float(payload.get("kellyFraction") or 0.0),
            quote=quote,
            timeframes=dict(payload.get("timeframes") or {}),
            provenance=dict(payload.get("provenance") or {}),
            notes=[str(n) for n in (payload.get("notes") or [])],
            created_ts=float(payload.get("createdTs") or 0.0),
            signal_id=signal_id,
        )


def rnd(value: Any, digits: int = 4) -> Any:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return value
    if not np.isfinite(f):
        return None
    return round(f, digits)


def plain(value: Any) -> Any:
    """JSON-safe coercion for numpy scalars and containers."""
    if isinstance(value, (np.floating, np.integer)):
        value = value.item()
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, float) and not np.isfinite(value):
        return None
    if isinstance(value, np.ndarray):
        return [plain(v) for v in value.tolist()]
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    if isinstance(value, Enum):
        return value.value
    return value


__all__ = [
    "Direction", "Archetype", "Regime", "Decision", "Readiness",
    "Candles", "Quote", "GateResult", "IndicatorReading", "Levels", "Signal",
    "rnd", "plain",
]
