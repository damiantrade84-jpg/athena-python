"""Read-only dynamic MT5 and Bybit universe discovery."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from .models import GhostInstrument
from .symbols import CanonicalSymbolService


HistoryChecker = Callable[[str], bool]


@dataclass(frozen=True, slots=True)
class UniverseResult:
    instruments: tuple[GhostInstrument, ...]
    errors: tuple[str, ...] = ()

    @property
    def discovered_count(self) -> int:
        return len(self.instruments)

    @property
    def eligible_count(self) -> int:
        return sum(item.eligible_for_scoring for item in self.instruments)

    @property
    def skipped_count(self) -> int:
        return self.discovered_count - self.eligible_count

    @property
    def group_counts(self) -> dict[str, int]:
        return dict(sorted(Counter(item.asset_group.value for item in self.instruments).items()))


def _ordered(items: list[GhostInstrument]) -> tuple[GhostInstrument, ...]:
    return tuple(sorted(items, key=lambda item: (item.canonical_symbol, item.broker_symbol)))


class MT5UniverseProvider:
    def __init__(
        self,
        client: Any,
        symbols: CanonicalSymbolService,
        *,
        history_checker: HistoryChecker | None = None,
        disabled_symbols: tuple[str, ...] = (),
        allowed_broker_symbols: tuple[str, ...] | None = None,
        now: Callable[[], datetime] | None = None,
    ):
        self._client = client
        self._symbols = symbols
        self._history_checker = history_checker
        self._disabled_symbols = set(disabled_symbols)
        self._allowed_broker_symbols = (
            None
            if allowed_broker_symbols is None
            else {str(symbol).strip().upper() for symbol in allowed_broker_symbols}
        )
        self._now = now or (lambda: datetime.now(timezone.utc))

    def discover(self) -> UniverseResult:
        try:
            raw_symbols = self._client.symbols_get()
        except Exception as exc:
            return UniverseResult((), (f"mt5_discovery_failed:{exc}",))
        if raw_symbols is None:
            return UniverseResult((), ("mt5_discovery_failed:no_symbols",))

        instruments: list[GhostInstrument] = []
        errors: list[str] = []
        for info in raw_symbols:
            try:
                instrument = self._symbols.classify_mt5(info)
            except Exception as exc:
                symbol = str(getattr(info, "name", "") or "<unknown>")
                errors.append(f"mt5_instrument_failed:{symbol}:{exc}")
                continue
            if (
                self._allowed_broker_symbols is not None
                and instrument.broker_symbol.strip().upper()
                not in self._allowed_broker_symbols
            ):
                continue
            reasons: list[str] = []
            if instrument.broker_symbol in self._disabled_symbols:
                reasons.append("disabled_by_config")
            visible = bool(getattr(info, "visible", False))
            if not visible:
                try:
                    selectable = bool(self._client.symbol_select(instrument.broker_symbol, True))
                except Exception:
                    selectable = False
                if not selectable:
                    reasons.append("symbol_not_selectable")
            trade_mode = getattr(info, "trade_mode", None)
            disabled_mode = getattr(self._client, "SYMBOL_TRADE_MODE_DISABLED", 0)
            trade_enabled = trade_mode is not None and trade_mode != disabled_mode
            if not trade_enabled:
                reasons.append("trade_disabled")
            expiration = getattr(info, "expiration_time", 0) or 0
            if expiration and float(expiration) <= self._now().timestamp():
                reasons.append("expired")
            if not reasons and self._history_checker is not None:
                try:
                    history_ok = bool(self._history_checker(instrument.broker_symbol))
                except Exception:
                    history_ok = False
                if not history_ok:
                    reasons.append("insufficient_history")
            instruments.append(
                replace(
                    instrument,
                    trade_enabled=trade_enabled,
                    skip_reasons=tuple(dict.fromkeys(reasons)),
                )
            )
        return UniverseResult(_ordered(instruments), tuple(errors))


class BybitUniverseProvider:
    def __init__(
        self,
        client: Any,
        symbols: CanonicalSymbolService,
        *,
        allow_spot: bool = False,
        history_checker: HistoryChecker | None = None,
        minimum_turnover: float | None = None,
        allowed_canonical_symbols: tuple[str, ...] | None = None,
    ):
        self._client = client
        self._symbols = symbols
        self._allow_spot = allow_spot
        self._history_checker = history_checker
        self._minimum_turnover = minimum_turnover
        self._allowed_canonical_symbols = (
            None
            if allowed_canonical_symbols is None
            else {str(symbol).strip().upper() for symbol in allowed_canonical_symbols}
        )

    def discover(self) -> UniverseResult:
        try:
            markets = self._client.load_markets()
        except Exception as exc:
            return UniverseResult((), (f"bybit_discovery_failed:{exc}",))
        if not isinstance(markets, Mapping):
            return UniverseResult((), ("bybit_discovery_failed:invalid_markets",))

        instruments: list[GhostInstrument] = []
        errors: list[str] = []
        for market in markets.values():
            if not isinstance(market, Mapping):
                errors.append("bybit_instrument_failed:<unknown>:invalid_market")
                continue
            try:
                instrument = self._symbols.classify_bybit(market)
            except Exception as exc:
                symbol = str(market.get("id") or market.get("symbol") or "<unknown>")
                errors.append(f"bybit_instrument_failed:{symbol}:{exc}")
                continue
            if (
                self._allowed_canonical_symbols is not None
                and instrument.canonical_symbol.strip().upper()
                not in self._allowed_canonical_symbols
            ):
                continue
            reasons: list[str] = []
            info = market.get("info") if isinstance(market.get("info"), Mapping) else {}
            status = str(info.get("status") or "Trading").strip().lower()
            active = market.get("active") is not False and status == "trading"
            if not active:
                reasons.append("inactive")
            linear_usdt = bool(market.get("swap")) and bool(market.get("linear")) and instrument.quote_asset == "USDT"
            spot_enabled = bool(market.get("spot")) and self._allow_spot
            if not linear_usdt and not spot_enabled:
                reasons.append("category_not_enabled")
            precision = market.get("precision") if isinstance(market.get("precision"), Mapping) else {}
            if precision.get("price") is None:
                reasons.append("missing_price_filter")
            limits = market.get("limits") if isinstance(market.get("limits"), Mapping) else {}
            amount_limits = limits.get("amount") if isinstance(limits.get("amount"), Mapping) else {}
            if precision.get("amount") is None or amount_limits.get("min") is None:
                reasons.append("missing_quantity_filter")
            if self._minimum_turnover is not None:
                raw_turnover = info.get("turnover24h")
                if raw_turnover not in (None, ""):
                    try:
                        if float(raw_turnover) < self._minimum_turnover:
                            reasons.append("below_liquidity_threshold")
                    except (TypeError, ValueError):
                        reasons.append("invalid_turnover")
            if not reasons and self._history_checker is not None:
                try:
                    history_ok = bool(self._history_checker(str(market.get("symbol") or instrument.broker_symbol)))
                except Exception:
                    history_ok = False
                if not history_ok:
                    reasons.append("insufficient_history")
            instruments.append(
                replace(
                    instrument,
                    trade_enabled=active,
                    skip_reasons=tuple(dict.fromkeys(reasons)),
                )
            )
        return UniverseResult(_ordered(instruments), tuple(errors))
