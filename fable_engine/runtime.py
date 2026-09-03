"""Lazy adapters connecting FABLE to the application's broker, data and feed infrastructure.

Nothing in this module is imported by the analytical core. ``athena.py`` hands
over a small runtime namespace (CONFIG, AUDIT_DB, fetch_mt5, fetch_bybit_klines,
fetch_bybit_ticker, active_pairs, kill_switch, log) and this module wires the
shared MT5 / Bybit executors, the shared context feeds (carry, COT, vol skew,
Bybit funding, event risk) and the FABLE service together.
"""

from __future__ import annotations

from pathlib import Path
import time
from typing import Any

from athena_app.services.mt5_time_alignment import normalize_mt5_tick_epoch_utc

from .config import load_fable_config
from .execution import FableExecutionCoordinator, FableExecutionError
from .market_data import FableMarketDataProvider
from .models import Quote
from .persistence import FableRepository
from .service import FableService


class RuntimeBrokerGateway:
    def __init__(self, runtime: Any) -> None:
        self.runtime = runtime

    def quote(self, signal: dict[str, Any]) -> Quote:
        venue = str(signal.get("venue") or "").lower()
        if venue == "bybit":
            symbol = str(signal.get("symbol") or signal.get("pair") or "").replace("/", "").upper()
            raw = self.runtime.fetch_bybit_ticker(symbol)
            if not isinstance(raw, dict):
                raise FableExecutionError("QUOTE_UNAVAILABLE")
            bid = float(raw.get("bid") or 0.0)
            ask = float(raw.get("ask") or 0.0)
            timestamp = float(raw.get("ts") or 0.0)
            if bid <= 0 or ask <= 0 or ask < bid or timestamp <= 0:
                raise FableExecutionError("QUOTE_INVALID")
            return Quote(venue="bybit", symbol=symbol, bid=bid, ask=ask, timestamp=timestamp, source=str(raw.get("source") or "bybit_rest"))

        import mt5_executor

        if not mt5_executor.mt5_connect():
            raise FableExecutionError("MT5_NOT_CONNECTED")
        mt5 = mt5_executor._get_mt5()
        display = str(signal.get("pair") or signal.get("symbol") or "")
        broker_symbol = mt5_executor.mt5_map_symbol(display)
        if mt5 is None or not broker_symbol:
            raise FableExecutionError("MT5_SYMBOL_UNAVAILABLE")
        if not mt5.symbol_select(broker_symbol, True):
            raise FableExecutionError("MT5_SYMBOL_UNAVAILABLE")
        tick = mt5.symbol_info_tick(broker_symbol)
        if tick is None:
            raise FableExecutionError("QUOTE_UNAVAILABLE")
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
            raise FableExecutionError("QUOTE_INVALID")
        return Quote(venue="mt5", symbol=str(broker_symbol), bid=bid, ask=ask, timestamp=float(timestamp), source="mt5_tick")

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


class RuntimeContextFeeds:
    """Shared advisory feeds for the chorus act. Every voice fails open to ``None``."""

    def __init__(self, runtime: Any) -> None:
        self.runtime = runtime

    @staticmethod
    def _safe(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception:
            return None

    def gather(self, pair: dict[str, Any]) -> dict[str, Any]:
        display = str(pair.get("display") or pair.get("symbol") or "")
        asset_type = str(pair.get("type") or "").strip().lower()
        context: dict[str, Any] = {}
        if asset_type == "forex":
            import carry_feed

            carry = self._safe(carry_feed.get_carry_z, display)
            context["carryZ"] = None if carry in (None, 0.0) else float(carry)
        if asset_type in {"forex", "commodity", "index"}:
            import cot_feed

            cot = self._safe(cot_feed.get_cot_z, display)
            context["cotZ"] = None if cot in (None, 0.0) else float(cot)
        import vol_skew_feed

        skew = self._safe(vol_skew_feed.get_vol_skew_z, display)
        context["volSkewZ"] = None if skew is None else float(skew)
        if asset_type == "crypto":
            import data_feeds

            symbol = str(pair.get("symbol") or display).replace("/", "").upper()
            funding = self._safe(data_feeds._fetch_bybit_funding_rate, symbol)
            if isinstance(funding, dict) and not funding.get("error"):
                context["fundingRate"] = float(funding.get("rate") or 0.0)
            else:
                context["fundingRate"] = None
        try:
            import event_risk
        except Exception:  # pragma: no cover - import failure is treated as feed unavailable
            event_risk = None
        risk = self._safe(event_risk.check_event_risk, display, asset_type, 4) if event_risk is not None else None
        if isinstance(risk, dict):
            context["eventRisk"] = {
                "allowed": bool(risk.get("allowed", True)),
                "reason": risk.get("reason"),
                "events": risk.get("events") or [],
            }
        elif bool(self.runtime.CONFIG.get("EVENT_RISK_ENABLED", False)):
            # The calendar is switched on but produced nothing: missing safety
            # data rejects by default rather than reading as "no events".
            context["eventRisk"] = {"allowed": False, "reason": "EVENT_RISK_FEED_UNAVAILABLE", "events": []}
        return context


def build_fable_service(runtime: Any) -> FableService:
    config = load_fable_config(runtime.CONFIG)
    database = Path(runtime.AUDIT_DB).with_name("fable_engine.db")
    repository = FableRepository(database)
    market_data = FableMarketDataProvider(
        config=config,
        fetch_mt5=runtime.fetch_mt5,
        fetch_bybit=runtime.fetch_bybit_klines,
    )
    gateway = RuntimeBrokerGateway(runtime)
    execution = FableExecutionCoordinator(
        config=config,
        repository=repository,
        gateway=gateway,
        root_config=runtime.CONFIG,
        kill_switch_fn=runtime.kill_switch,
        now_fn=time.time,
    )
    return FableService(
        config=config,
        repository=repository,
        market_data=market_data,
        pair_provider=runtime.active_pairs,
        execution=execution,
        log=runtime.log,
        context_feeds=RuntimeContextFeeds(runtime),
    )
