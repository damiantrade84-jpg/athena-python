"""Lazy adapters that connect Ghost Trade to Athena's shared runtime services."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import load_ghost_config
from .market_data import SharedCandleProvider
from .models import AssetGroup, Venue
from .persistence import GhostRepository
from .service import GhostService
from .symbols import CanonicalSymbolService
from .universe import BybitUniverseProvider, MT5UniverseProvider


class _LazyMT5Client:
    """Resolve Athena's existing MT5 client only when discovery is requested."""

    def _client(self):
        import mt5_executor

        if not mt5_executor.mt5_connect():
            raise RuntimeError("MT5 not connected")
        client = mt5_executor._get_mt5()
        if client is None:
            raise RuntimeError("MT5 client unavailable")
        return client

    @property
    def SYMBOL_TRADE_MODE_DISABLED(self):
        return getattr(self._client(), "SYMBOL_TRADE_MODE_DISABLED", 0)

    def symbols_get(self):
        return self._client().symbols_get()

    def symbol_select(self, symbol, enabled):
        return self._client().symbol_select(symbol, enabled)


class _LazyBybitClient:
    """Resolve Athena's existing Bybit client only when discovery is requested."""

    def load_markets(self):
        import bybit_executor

        client = bybit_executor._get_exchange()
        if client is None:
            raise RuntimeError("Bybit client unavailable")
        return client.load_markets()


def _asset_type(group: AssetGroup) -> str:
    return {
        AssetGroup.FOREX: "forex",
        AssetGroup.CRYPTO: "crypto",
        AssetGroup.METALS: "commodity",
        AssetGroup.ENERGY: "commodity",
        AssetGroup.COMMODITIES_OTHER: "commodity",
        AssetGroup.INDICES: "index",
        AssetGroup.EQUITIES: "stock",
        AssetGroup.OTHER: "other",
    }[group]


def build_ghost_trade_service(runtime: Any) -> GhostService:
    config = load_ghost_config(runtime.CONFIG)
    audit_path = Path(runtime.AUDIT_DB)
    repository = GhostRepository(audit_path.with_name("ghost_trade.db"))
    repository.migrate()
    symbols = CanonicalSymbolService(config.symbol_overrides)
    mt5_client = getattr(runtime, "mt5_client", None) or _LazyMT5Client()
    bybit_client = getattr(runtime, "bybit_client", None) or _LazyBybitClient()

    def load_candles(instrument, timeframe: str, limit: int):
        if instrument.venue is Venue.MT5:
            return runtime.fetch_mt5(
                {
                    "symbol": instrument.broker_symbol,
                    "display": instrument.canonical_symbol,
                    "type": _asset_type(instrument.asset_group),
                    "source": "mt5",
                },
                timeframe,
                limit,
            )
        return runtime.fetch_bybit_klines(
            instrument.broker_symbol,
            timeframe,
            limit,
        )

    candle_provider = SharedCandleProvider(load_candles)
    universe_providers = (
        MT5UniverseProvider(mt5_client, symbols),
        BybitUniverseProvider(bybit_client, symbols, allow_spot=False),
    )
    return GhostService(
        config=config,
        repository=repository,
        universe_providers=universe_providers,
        candle_provider=candle_provider,
    )
