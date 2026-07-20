from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from typing import Any, Callable, Mapping

from .constants import TIMEFRAME_SECONDS


class ProviderDataError(RuntimeError):
    def __init__(self, code: str, message: str, *, provider: str, symbol: str, timeframe: str):
        super().__init__(message)
        self.code = code
        self.provider = provider
        self.symbol = symbol
        self.timeframe = timeframe

    def to_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "message": str(self),
            "provider": self.provider,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
        }


@dataclass(frozen=True)
class ProviderSeries:
    provider: str
    provider_symbol: str
    candles: list[dict[str, Any]]
    adjustment_status: str
    source_detail: str


def _symbol_key(value: Any) -> str:
    return "".join(character for character in str(value or "").upper() if character.isalnum())


def _extract_candles(response: Any, *, provider: str, symbol: str, timeframe: str) -> list[dict[str, Any]]:
    if isinstance(response, dict):
        if response.get("fallback_provider"):
            raise ProviderDataError(
                "PROVIDER_FALLBACK_REJECTED",
                f"{provider} returned fallback data from {response.get('fallback_provider')}",
                provider=provider,
                symbol=symbol,
                timeframe=timeframe,
            )
        if response.get("error"):
            raise ProviderDataError(
                "PROVIDER_FETCH_FAILED",
                str(response.get("detail") or response.get("error") or "provider fetch failed"),
                provider=provider,
                symbol=symbol,
                timeframe=timeframe,
            )
        response = response.get("candles")
    if response is None:
        raise ProviderDataError(
            "PROVIDER_DATA_MISSING",
            "provider returned no candles",
            provider=provider,
            symbol=symbol,
            timeframe=timeframe,
        )
    if not isinstance(response, list):
        raise ProviderDataError(
            "PROVIDER_RESPONSE_MALFORMED",
            f"expected candle list, received {type(response).__name__}",
            provider=provider,
            symbol=symbol,
            timeframe=timeframe,
        )
    return [dict(row) for row in response if isinstance(row, dict)]


def _bar_timestamp(row: Mapping[str, Any]) -> datetime | None:
    raw = row.get("time") or row.get("datetime") or row.get("open_time") or row.get("timestamp")
    if raw is None:
        return None
    if isinstance(raw, datetime):
        parsed = raw
    elif isinstance(raw, (int, float)):
        value = float(raw)
        if value > 1e12:
            value /= 1000.0
        parsed = datetime.fromtimestamp(value, tz=timezone.utc)
    else:
        text = str(raw).strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            try:
                parsed = datetime.strptime(text[:10], "%Y-%m-%d")
            except ValueError:
                return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class RuntimeProviderAdapter:
    """Strict adapter over injected production provider callables.

    It never calls the legacy backtest cache and never accepts a provider's
    fallback payload.  A resolver supplied by the app freezes the configured
    provider identity before ingestion.
    """

    def __init__(
        self,
        *,
        pairs: list[dict[str, Any]],
        provider_resolver: Callable[[dict[str, Any], str], str],
        fetchers: Mapping[str, Callable[..., Any]],
        symbol_resolvers: Mapping[str, Callable[[dict[str, Any]], str]] | None = None,
    ):
        self._pairs = [dict(pair) for pair in pairs]
        self._provider_resolver = provider_resolver
        self._fetchers = {str(key).lower(): value for key, value in fetchers.items() if callable(value)}
        self._symbol_resolvers = {
            str(key).lower(): value for key, value in (symbol_resolvers or {}).items() if callable(value)
        }
        self._pair_by_key: dict[str, dict[str, Any]] = {}
        for pair in self._pairs:
            for raw in (pair.get("display"), pair.get("symbol"), pair.get("pair")):
                key = _symbol_key(raw)
                if key:
                    self._pair_by_key.setdefault(key, pair)

    def resolve_pair(self, symbol: str) -> dict[str, Any] | None:
        pair = self._pair_by_key.get(_symbol_key(symbol))
        return dict(pair) if pair else None

    def list_pairs(self) -> list[dict[str, Any]]:
        return [dict(pair) for pair in self._pairs]

    def provider_identity(self, pair: dict[str, Any], engine: str) -> tuple[str, str]:
        provider = str(self._provider_resolver(pair, engine) or "").strip().lower()
        if not provider:
            raise ProviderDataError(
                "PROVIDER_MAPPING_MISSING",
                "configured production provider mapping is unavailable",
                provider="unknown",
                symbol=str(pair.get("display") or pair.get("symbol") or ""),
                timeframe="",
            )
        symbol_resolver = self._symbol_resolvers.get(provider)
        provider_symbol = str(symbol_resolver(pair) if symbol_resolver else pair.get("symbol") or pair.get("display") or "").strip()
        if not provider_symbol:
            raise ProviderDataError(
                "PROVIDER_SYMBOL_MISSING",
                "provider symbol mapping is unavailable",
                provider=provider,
                symbol=str(pair.get("display") or ""),
                timeframe="",
            )
        return provider, provider_symbol

    @staticmethod
    def estimate_bar_count(start_date: date, end_date: date, timeframe: str, asset_type: str) -> int:
        seconds = TIMEFRAME_SECONDS.get(str(timeframe).upper())
        if not seconds:
            raise ValueError(f"unsupported timeframe {timeframe!r}")
        days = max(1, (end_date - start_date).days + 1)
        market_fraction = 1.0 if str(asset_type).lower() == "crypto" else 5.0 / 7.0
        estimate = math.ceil(days * 86_400 / seconds * market_fraction)
        if str(asset_type).lower() == "crypto":
            # A date-bounded 24/7 series needs no 15% session-calendar buffer.
            return max(250, int(estimate) + 10)
        return max(250, int(estimate * 1.15) + 10)

    def fetch(
        self,
        pair: dict[str, Any],
        *,
        engine: str,
        timeframe: str,
        start_date: date,
        end_date: date,
    ) -> ProviderSeries:
        provider, provider_symbol = self.provider_identity(pair, engine)
        fetcher = self._fetchers.get(provider)
        display = str(pair.get("display") or pair.get("symbol") or provider_symbol)
        if fetcher is None:
            raise ProviderDataError(
                "PROVIDER_ADAPTER_UNAVAILABLE",
                f"no v2 direct adapter registered for {provider}",
                provider=provider,
                symbol=display,
                timeframe=timeframe,
            )
        limit = self.estimate_bar_count(start_date, end_date, timeframe, str(pair.get("type") or ""))
        try:
            if provider == "binance":
                interval = {
                    "D1": "1d", "H4": "4h", "H1": "1h", "M30": "30m",
                    "M15": "15m", "M5": "5m", "M1": "1m",
                }.get(str(timeframe).upper())
                if interval is None:
                    raise ValueError(f"unsupported Binance timeframe {timeframe!r}")
                response = fetcher(provider_symbol, interval, limit)
            elif provider == "bybit":
                response = fetcher(
                    provider_symbol,
                    timeframe,
                    limit,
                    start_ms=int(datetime.combine(start_date, time.min, tzinfo=timezone.utc).timestamp() * 1000),
                    end_ms=int(datetime.combine(end_date, time.max, tzinfo=timezone.utc).timestamp() * 1000),
                )
            elif provider == "yfinance":
                response = fetcher(provider_symbol, timeframe, limit)
            else:
                response = fetcher(pair, timeframe, limit)
        except Exception as exc:
            raise ProviderDataError(
                "PROVIDER_FETCH_EXCEPTION",
                str(exc),
                provider=provider,
                symbol=display,
                timeframe=timeframe,
            ) from exc
        candles = _extract_candles(response, provider=provider, symbol=display, timeframe=timeframe)
        start_dt = datetime.combine(start_date, time.min, tzinfo=timezone.utc)
        end_dt = datetime.combine(end_date, time.max, tzinfo=timezone.utc)
        trimmed = []
        for candle in candles:
            timestamp = _bar_timestamp(candle)
            if timestamp is not None and start_dt <= timestamp <= end_dt:
                trimmed.append(candle)
        if not trimmed:
            raise ProviderDataError(
                "PROVIDER_DATE_COVERAGE_MISSING",
                f"no {timeframe} bars within requested date range",
                provider=provider,
                symbol=display,
                timeframe=timeframe,
            )
        adjustment = "adjusted" if provider == "yfinance" else "raw"
        return ProviderSeries(
            provider=provider,
            provider_symbol=provider_symbol,
            candles=trimmed,
            adjustment_status=adjustment,
            source_detail=f"direct:{provider}",
        )
