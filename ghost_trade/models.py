"""Immutable domain contracts for the standalone Ghost Trade engine."""

from __future__ import annotations

from dataclasses import dataclass, field
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
