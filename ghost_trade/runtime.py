"""Lazy adapters that connect Ghost Trade to Athena's shared runtime services."""

from __future__ import annotations

from pathlib import Path
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

from .config import load_ghost_config
from .market_data import SharedCandleProvider
from .models import AssetGroup, Venue
from .persistence import GhostRepository
from .position_sizing import SizeResult, size_bybit, size_mt5
from .risk import GhostRiskService
from .service import GhostService
from .symbols import CanonicalSymbolService
from .universe import BybitUniverseProvider, MT5UniverseProvider
from .execution.bybit_demo import BybitDemoAdapter
from .execution.coordinator import GhostExecutionCoordinator
from .execution.mt5_demo import MT5DemoAdapter


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

    def account_info(self):
        return self._client().account_info()


class _LazyBybitClient:
    """Resolve Athena's existing Bybit client only when discovery is requested."""

    def load_markets(self):
        import bybit_executor

        client = bybit_executor._get_exchange()
        if client is None:
            raise RuntimeError("Bybit client unavailable")
        return client.load_markets()

    def _client(self):
        import bybit_executor

        client = bybit_executor._get_exchange()
        if client is None:
            raise RuntimeError("Bybit client unavailable")
        return client

    @property
    def urls(self):
        return self._client().urls

    @property
    def options(self):
        return self._client().options

    @property
    def hostname(self):
        return self._client().hostname


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
    service = GhostService(
        config=config,
        repository=repository,
        universe_providers=universe_providers,
        candle_provider=candle_provider,
    )

    def mt5_connect():
        if hasattr(runtime, "mt5_connect_fn"):
            return bool(runtime.mt5_connect_fn())
        try:
            import mt5_executor
            return bool(mt5_executor.mt5_connect())
        except Exception:
            return False

    def mt5_execute(payload, approval):
        import mt5_executor
        return mt5_executor.mt5_execute(payload, approval)

    def mt5_close(ticket, volume=None):
        import mt5_executor
        return mt5_executor.mt5_close_position(ticket, volume=volume)

    def bybit_execute(payload, approval):
        import bybit_executor
        return bybit_executor.bybit_execute(payload, approval)

    def bybit_close(pair, direction, volume):
        import bybit_executor
        return bybit_executor.bybit_close_position(pair, direction, volume)

    adapters = {
        Venue.MT5: MT5DemoAdapter(
            mt5_client, connect_fn=mt5_connect, execute_fn=mt5_execute,
            close_fn=mt5_close,
        ),
        Venue.BYBIT: BybitDemoAdapter(
            bybit_client, execute_fn=bybit_execute, close_fn=bybit_close,
        ),
    }

    def account_provider(venue: Venue):
        if hasattr(runtime, "ghost_account_provider"):
            return runtime.ghost_account_provider(venue)
        if venue is Venue.MT5:
            import mt5_executor
            return mt5_executor.mt5_get_account()
        import bybit_executor
        return bybit_executor.bybit_get_account()

    def positions_provider(venue: Venue):
        if hasattr(runtime, "ghost_positions_provider"):
            return runtime.ghost_positions_provider(venue)
        if venue is Venue.MT5:
            import mt5_executor
            response = mt5_executor.mt5_get_positions()
        else:
            import bybit_executor
            response = bybit_executor.bybit_get_positions()
        if isinstance(response, dict):
            if response.get("error"):
                raise RuntimeError(str(response.get("detail") or "positions unavailable"))
            return list(response.get("positions") or [])
        return list(response or [])

    def symbol_info_provider(signal):
        if hasattr(runtime, "ghost_symbol_info_provider"):
            return runtime.ghost_symbol_info_provider(signal)
        if signal.instrument.venue is Venue.MT5:
            import mt5_executor
            return mt5_executor.mt5_get_symbol_info(signal.instrument.broker_symbol)
        import bybit_executor
        return bybit_executor.bybit_get_symbol_info(signal.instrument.broker_symbol)

    def size_fn(signal, account, info) -> SizeResult:
        equity = account.get("equity")
        if signal.instrument.venue is Venue.MT5:
            return size_mt5(
                equity=equity,
                risk_fraction=config.risk_per_trade,
                entry=signal.entry,
                stop=signal.stop,
                symbol_info=SimpleNamespace(**info),
                account_conversion_rate=info.get("account_conversion_rate"),
            )
        return size_bybit(
            equity=equity,
            risk_fraction=config.risk_per_trade,
            entry=signal.entry,
            stop=signal.stop,
            quantity_step=info.get("volume_step"),
            minimum_quantity=info.get("volume_min"),
            maximum_quantity=info.get("volume_max"),
            minimum_notional=info.get("minimum_notional", 5.0),
            volatility_regime=signal.volatility_regime,
        )

    def guardian_fn(payload, positions, account):
        import guardian
        return guardian.pre_trade_check(payload, positions, account)

    def approval_fn(payload, account, positions, info, size):
        import risk_engine
        approval = risk_engine.risk_check(
            payload,
            account_balance=account.get("balance"),
            account_equity=account.get("equity"),
            open_positions=positions,
            symbol_info=info,
            kill_switch=(
                bool(runtime.kill_switch()) if hasattr(runtime, "kill_switch") else False
            ),
            execution_context="ghost_trade_demo",
        )
        if approval.approved and approval.volume > float(size.quantity or 0):
            approval = replace(
                approval,
                volume=float(size.quantity or 0),
                risk_amount=float(size.estimated_risk_cash or 0),
                risk_pct=config.risk_per_trade,
                reason="OK_GHOST_DOWNSIZED",
            )
        return approval

    coordinator = GhostExecutionCoordinator(
        config=config,
        repository=repository,
        risk_service=GhostRiskService(config),
        adapters=adapters,
        account_provider=account_provider,
        positions_provider=positions_provider,
        symbol_info_provider=symbol_info_provider,
        size_fn=size_fn,
        guardian_fn=guardian_fn,
        approval_fn=approval_fn,
    )
    service.set_execution_coordinator(coordinator)
    return service
