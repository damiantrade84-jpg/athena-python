"""Lazy adapters connecting SOL to the application's broker/data infrastructure."""

from __future__ import annotations

from pathlib import Path
import time
from typing import Any

from athena_app.services.mt5_time_alignment import normalize_mt5_tick_epoch_utc

from .config import load_sol_config
from .execution import SolExecutionCoordinator, SolExecutionError
from .market_data import SolMarketDataProvider
from .models import Quote
from .persistence import SolRepository
from .service import SolService


class RuntimeBrokerGateway:
    def __init__(self, runtime: Any) -> None:
        self.runtime = runtime

    def quote(self, signal: dict[str, Any]) -> Quote:
        venue = str(signal.get("venue") or "").lower()
        if venue == "bybit":
            symbol = str(signal.get("symbol") or signal.get("pair") or "").replace("/", "").upper()
            raw = self.runtime.fetch_bybit_ticker(symbol)
            if not isinstance(raw, dict):
                raise SolExecutionError("QUOTE_UNAVAILABLE")
            bid = float(raw.get("bid") or 0.0)
            ask = float(raw.get("ask") or 0.0)
            timestamp = float(raw.get("ts") or 0.0)
            if bid <= 0 or ask <= 0 or ask < bid or timestamp <= 0:
                raise SolExecutionError("QUOTE_INVALID")
            return Quote(
                venue="bybit",
                symbol=symbol,
                bid=bid,
                ask=ask,
                timestamp=timestamp,
                source=str(raw.get("source") or "bybit_rest"),
            )

        import mt5_executor

        if not mt5_executor.mt5_connect():
            raise SolExecutionError("MT5_NOT_CONNECTED")
        mt5 = mt5_executor._get_mt5()
        display = str(signal.get("pair") or signal.get("symbol") or "")
        broker_symbol = mt5_executor.mt5_map_symbol(display)
        if mt5 is None or not broker_symbol:
            raise SolExecutionError("MT5_SYMBOL_UNAVAILABLE")
        if not mt5.symbol_select(broker_symbol, True):
            raise SolExecutionError("MT5_SYMBOL_UNAVAILABLE")
        tick = mt5.symbol_info_tick(broker_symbol)
        if tick is None:
            raise SolExecutionError("QUOTE_UNAVAILABLE")
        bid = float(getattr(tick, "bid", 0.0) or 0.0)
        ask = float(getattr(tick, "ask", 0.0) or 0.0)
        raw_epoch = float(getattr(tick, "time_msc", 0.0) or 0.0) / 1000.0
        if raw_epoch <= 0:
            raw_epoch = float(getattr(tick, "time", 0.0) or 0.0)
        timestamp = normalize_mt5_tick_epoch_utc(
            raw_epoch,
            time.time(),
            int(self.runtime.CONFIG.get("MT5_BROKER_UTC_OFFSET", 0) or 0),
        )
        if bid <= 0 or ask <= 0 or ask < bid or not timestamp:
            raise SolExecutionError("QUOTE_INVALID")
        return Quote(
            venue="mt5",
            symbol=str(broker_symbol),
            bid=bid,
            ask=ask,
            timestamp=float(timestamp),
            source="mt5_tick",
        )

    def account(self, venue: str) -> dict[str, Any]:
        if venue == "bybit":
            import bybit_executor

            return bybit_executor.bybit_get_account()
        import mt5_executor

        return mt5_executor.mt5_get_account()

    def positions(self, venue: str) -> dict[str, Any]:
        if venue == "bybit":
            import bybit_executor

            return bybit_executor.bybit_get_positions()
        import mt5_executor

        return mt5_executor.mt5_get_positions()

    def symbol_info(self, signal: dict[str, Any]) -> dict[str, Any]:
        display = str(signal.get("pair") or signal.get("symbol") or "")
        if signal.get("venue") == "bybit":
            import bybit_executor

            return bybit_executor.bybit_get_symbol_info(display)
        import mt5_executor

        return mt5_executor.mt5_get_symbol_info(display)

    def execute(self, venue: str, payload: dict[str, Any], approval: Any) -> dict[str, Any]:
        if venue == "bybit":
            import bybit_executor

            return bybit_executor.bybit_execute(payload, approval)
        import mt5_executor

        return mt5_executor.mt5_execute(payload, approval)


def build_sol_service(runtime: Any) -> SolService:
    config = load_sol_config(runtime.CONFIG)
    database = Path(runtime.AUDIT_DB).with_name("sol_engine.db")
    repository = SolRepository(database)
    market_data = SolMarketDataProvider(
        config=config,
        fetch_mt5=runtime.fetch_mt5,
        fetch_bybit=runtime.fetch_bybit_klines,
    )
    gateway = RuntimeBrokerGateway(runtime)
    execution = SolExecutionCoordinator(
        config=config,
        repository=repository,
        gateway=gateway,
        root_config=runtime.CONFIG,
        kill_switch_fn=runtime.kill_switch,
        now_fn=time.time,
    )
    return SolService(
        config=config,
        repository=repository,
        market_data=market_data,
        pair_provider=runtime.active_pairs,
        execution=execution,
        log=runtime.log,
    )

