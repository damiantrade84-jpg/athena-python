from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class InstrumentKind(str, Enum):
    SPOT_CFD = "spot_cfd"
    ENERGY_CFD = "energy_cfd"
    INDUSTRIAL_METAL_CFD = "industrial_metal_cfd"
    AGRICULTURAL_CFD = "agricultural_cfd"
    EXCHANGE_FUTURES_PROXY = "exchange_futures_proxy"


class PriceField(str, Enum):
    BID = "bid"
    ASK = "ask"
    MIDPOINT = "midpoint"
    UNKNOWN = "unknown"


class VolumeKind(str, Enum):
    TICK_VOLUME = "tick_volume"
    EXCHANGE_VOLUME = "exchange_volume"
    OVERLAY_VOLUME = "overlay_volume"
    UNKNOWN = "unknown"


class CostStatus(str, Enum):
    OBSERVED = "observed"
    MODELLED = "modelled"
    UNAVAILABLE = "unavailable"


class AcquisitionSource(str, Enum):
    MT5 = "mt5"
    EODHD = "eodhd"
    YFINANCE_FALLBACK = "yfinance_fallback"
    POLYGON_FALLBACK = "polygon_fallback"


@dataclass(frozen=True)
class ProviderAliases:
    canonical_display: str
    mt5_symbol: str | None
    eodhd_ticker: str | None
    eodhd_volume_ticker: str | None
    yfinance_symbol: str | None
    polygon_symbol: str | None
    cot_futures_proxy: str | None


@dataclass(frozen=True)
class InstrumentSpec:
    display: str
    symbol: str
    cluster_id: str
    cluster_label: str
    instrument_kind: InstrumentKind
    primary_source: AcquisitionSource
    h4_construction: str
    price_field: PriceField
    volume_kind: VolumeKind
    spread_history: CostStatus
    financing_history: CostStatus
    rollover_method: str
    eodhd_h4_supported: bool
    eodhd_h1_depth_days: int
    eodhd_d1_depth_days: int
    mt5_native_h4: bool
    known_discontinuities: tuple[str, ...] = ()
    rejection_reason: str | None = None


@dataclass
class CandleQualityReport:
    timeframe: str
    bar_count: int
    first_timestamp: str | None
    last_timestamp: str | None
    duplicate_timestamps: list[str] = field(default_factory=list)
    missing_bar_estimate: int = 0
    future_bar_count: int = 0
    incomplete_bar_present: bool = False
    timezone: str = "UTC"
    confirmed_bar_semantics: str = "open_time_utc"
    details: dict[str, Any] = field(default_factory=dict)
