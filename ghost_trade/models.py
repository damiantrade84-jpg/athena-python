"""Immutable domain contracts for the standalone Ghost Trade engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping


class _StringEnum(str, Enum):
    def __str__(self) -> str:
        return self.value


class GhostMode(_StringEnum):
    SHADOW = "SHADOW"
    DEMO_MANUAL = "DEMO_MANUAL"
    DEMO_AUTO = "DEMO_AUTO"


class Venue(_StringEnum):
    MT5 = "MT5"
    BYBIT = "BYBIT"


class AssetGroup(_StringEnum):
    FOREX = "forex"
    CRYPTO = "crypto"
    METALS = "metals"
    ENERGY = "energy"
    COMMODITIES_OTHER = "commodities_other"
    INDICES = "indices"
    EQUITIES = "equities"
    OTHER = "other"


class Style(_StringEnum):
    INTRADAY = "intraday"
    SWING = "swing"


class Direction(_StringEnum):
    LONG = "LONG"
    SHORT = "SHORT"
    NEUTRAL = "NEUTRAL"


class VolatilityRegime(_StringEnum):
    COMPRESSED = "COMPRESSED"
    NORMAL = "NORMAL"
    EXPANDING = "EXPANDING"
    SQUEEZE_RELEASE = "SQUEEZE_RELEASE"
    EXTREME = "EXTREME"


class SignalStatus(_StringEnum):
    ANALYSED = "ANALYSED"
    ELIGIBLE = "ELIGIBLE"
    DISMISSED = "DISMISSED"
    STALE = "STALE"
    ERROR = "ERROR"


class PositionMode(_StringEnum):
    SHADOW = "SHADOW"
    DEMO = "DEMO"


class PositionStatus(_StringEnum):
    PENDING = "PENDING"
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    REJECTED = "REJECTED"
    UNSAFE = "UNSAFE"
    RECONCILIATION_MISMATCH = "RECONCILIATION_MISMATCH"


class DemoVerificationStatus(_StringEnum):
    DEMO_VERIFIED = "DEMO VERIFIED"
    DEMO_NOT_VERIFIED = "DEMO NOT VERIFIED"
    SHADOW_ONLY = "SHADOW ONLY"


class ExitStrategy(_StringEnum):
    STRUCTURAL = "STRUCTURAL"


class SwingKind(_StringEnum):
    HIGH = "HIGH"
    LOW = "LOW"


@dataclass(frozen=True, slots=True)
class Candle:
    open_time: datetime
    close_time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float | None = None

    def __post_init__(self) -> None:
        prices = tuple(float(value) for value in (self.open, self.high, self.low, self.close))
        if any(value != value or value in (float("inf"), float("-inf")) for value in prices):
            raise ValueError("candle prices must be finite")
        if self.high < self.low:
            raise ValueError("candle high cannot be below low")
        if not self.low <= self.open <= self.high or not self.low <= self.close <= self.high:
            raise ValueError("candle open and close must be inside its range")
        if self.close_time <= self.open_time:
            raise ValueError("candle close_time must be after open_time")


@dataclass(frozen=True, slots=True)
class Swing:
    kind: SwingKind
    center_index: int
    confirmation_index: int
    price: float
    center_time: datetime
    confirmation_time: datetime


@dataclass(frozen=True, slots=True)
class SwingSet:
    highs: tuple[Swing, ...] = ()
    lows: tuple[Swing, ...] = ()


@dataclass(frozen=True, slots=True)
class StructureDiagnostics:
    direction: float
    strength: float
    last_swing_highs: tuple[float, ...]
    last_swing_lows: tuple[float, ...]
    bars_since_structure_event: int | None
    ordered_fraction: float
    displacement_atr: float


@dataclass(frozen=True, slots=True)
class MomentumDiagnostics:
    direction: float
    normalized_roc: float
    normalized_pressure: float | None
    roc_available: bool
    pressure_available: bool
    volume_quality: float
    correlation: float | None


@dataclass(frozen=True, slots=True)
class TradeGeometry:
    direction: Direction
    entry: float
    unbuffered_stop: float
    stop: float
    target: float
    risk_distance: float
    target_distance: float
    raw_rr: float
    stop_swing_confirmation_index: int
    target_swing_confirmation_index: int


@dataclass(frozen=True, slots=True)
class GeometryResult:
    geometry: TradeGeometry | None
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class EntryQualityDiagnostics:
    score: float
    rr_score: float
    room_score: float
    pullback_score: float
    trigger_score: float


@dataclass(frozen=True, slots=True)
class ExhaustionDiagnostics:
    penalty: float
    adverse_wick: bool
    declining_participation: bool


@dataclass(frozen=True, slots=True)
class LiveOverlay:
    confirmed_score: float
    adjustment: float
    display_score: float
    diagnostics: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "diagnostics", _immutable_mapping(self.diagnostics))


@dataclass(frozen=True, slots=True)
class VolatilityDiagnostics:
    regime: VolatilityRegime
    atr_percentile: float
    bb_width_percentile: float
    prior_bb_width_percentile: float | None
    atr_rising: bool
    bb_width_rising: bool
    available: bool


@dataclass(frozen=True, slots=True)
class ConfirmedScoreResult:
    direction: Direction
    direction_confidence: float
    structure: StructureDiagnostics
    momentum: MomentumDiagnostics
    volatility: VolatilityDiagnostics
    geometry: GeometryResult
    entry_quality: EntryQualityDiagnostics
    exhaustion: ExhaustionDiagnostics
    confirmed_score: float
    selected: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class GhostSignal:
    signal_id: str
    signal_version: str
    scan_id: str
    instrument: GhostInstrument
    style: Style
    direction: Direction
    decision_time: datetime
    confirmed_score: float
    live_adjustment: float
    display_score: float
    direction_confidence: float
    entry_quality: float
    entry: float | None
    stop: float | None
    target: float | None
    raw_rr: float | None
    volatility_regime: VolatilityRegime
    can_execute: bool
    status: SignalStatus
    reasons: tuple[str, ...] = ()
    components: Mapping[str, Any] = field(default_factory=dict)
    confirmed_times: Mapping[str, str] = field(default_factory=dict)
    data_freshness: str = "UNKNOWN"
    spread: float | None = None
    group_rank: int | None = None
    group_count: int | None = None
    global_rank: int | None = None
    global_count: int | None = None

    def __post_init__(self) -> None:
        for label, value in (
            ("confirmed_score", self.confirmed_score),
            ("display_score", self.display_score),
            ("entry_quality", self.entry_quality),
        ):
            if not 0.0 <= float(value) <= 1.0:
                raise ValueError(f"{label} must be in [0, 1]")
        if not -0.10 <= float(self.live_adjustment) <= 0.10:
            raise ValueError("live_adjustment must be in [-0.10, 0.10]")
        if not -1.0 <= float(self.direction_confidence) <= 1.0:
            raise ValueError("direction_confidence must be in [-1, 1]")
        object.__setattr__(self, "reasons", tuple(self.reasons))
        object.__setattr__(self, "components", _immutable_mapping(self.components))
        object.__setattr__(self, "confirmed_times", _immutable_mapping(self.confirmed_times))

    def to_dict(self) -> dict[str, Any]:
        return {
            "signalId": self.signal_id,
            "signalVersion": self.signal_version,
            "scanId": self.scan_id,
            "instrument": self.instrument.to_dict(),
            "style": self.style.value,
            "direction": self.direction.value,
            "decisionTime": self.decision_time.isoformat(),
            "confirmedScore": self.confirmed_score,
            "liveAdjustment": self.live_adjustment,
            "displayScore": self.display_score,
            "directionConfidence": self.direction_confidence,
            "entryQuality": self.entry_quality,
            "entry": self.entry,
            "stop": self.stop,
            "target": self.target,
            "structuralRR": self.raw_rr,
            "volatilityRegime": self.volatility_regime.value,
            "canExecute": self.can_execute,
            "status": self.status.value,
            "reasons": list(self.reasons),
            "components": dict(self.components),
            "confirmedTimes": dict(self.confirmed_times),
            "dataFreshness": self.data_freshness,
            "spread": self.spread,
            "groupRank": self.group_rank,
            "groupCount": self.group_count,
            "globalRank": self.global_rank,
            "globalCount": self.global_count,
        }


@dataclass(frozen=True, slots=True)
class GhostPosition:
    position_id: str
    signal_id: str
    venue: Venue
    canonical_symbol: str
    asset_group: AssetGroup
    mode: str
    status: str
    direction: Direction
    entry: float
    stop: float
    target: float
    initial_risk: float
    quantity: float | None
    opened_at: datetime | None
    closed_at: datetime | None
    volatility_regime: VolatilityRegime
    entry_quality: float
    signal_score: float
    broker_position_id: str | None = None
    payload: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "payload", _immutable_mapping(self.payload))


def _immutable_mapping(value: Mapping[str, Any] | None) -> Mapping[str, Any]:
    return MappingProxyType(dict(value or {}))


@dataclass(frozen=True, slots=True)
class GhostInstrument:
    venue: Venue
    broker_symbol: str
    canonical_symbol: str
    asset_group: AssetGroup
    asset_subgroup: str = "other"
    base_asset: str = ""
    quote_asset: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)
    trade_enabled: bool = True
    skip_reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.broker_symbol.strip():
            raise ValueError("broker_symbol is required")
        if not self.canonical_symbol.strip():
            raise ValueError("canonical_symbol is required")
        object.__setattr__(self, "metadata", _immutable_mapping(self.metadata))
        object.__setattr__(self, "skip_reasons", tuple(self.skip_reasons))

    @property
    def eligible_for_scoring(self) -> bool:
        return self.trade_enabled and not self.skip_reasons

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.venue.value,
            "brokerSymbol": self.broker_symbol,
            "canonicalSymbol": self.canonical_symbol,
            "assetGroup": self.asset_group.value,
            "assetSubgroup": self.asset_subgroup,
            "baseAsset": self.base_asset,
            "quoteAsset": self.quote_asset,
            "metadata": dict(self.metadata),
            "tradeEnabled": self.trade_enabled,
            "eligibleForScoring": self.eligible_for_scoring,
            "skipReasons": list(self.skip_reasons),
        }
