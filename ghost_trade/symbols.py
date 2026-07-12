"""Metadata-first canonical symbol and asset-group classification."""

from __future__ import annotations

import re
from typing import Any, Mapping

from .models import AssetGroup, GhostInstrument, Venue


_FOREX_CURRENCIES = {
    "USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD",
    "SEK", "NOK", "DKK", "SGD", "HKD", "ZAR", "MXN", "TRY", "PLN",
    "HUF", "CZK", "CNH", "CNY",
}


def _value(source: Any, key: str, default: Any = None) -> Any:
    if isinstance(source, Mapping):
        return source.get(key, default)
    return getattr(source, key, default)


def _clean_asset(value: Any) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", str(value or "")).upper()


class CanonicalSymbolService:
    def __init__(self, overrides: Mapping[str, Mapping[str, Any]] | None = None):
        self._overrides = {str(key): dict(value) for key, value in (overrides or {}).items()}

    def classify_mt5(self, info: Any) -> GhostInstrument:
        broker_symbol = str(_value(info, "name", "") or "").strip()
        override = self._overrides.get(broker_symbol)
        if override is not None:
            return self._from_override(Venue.MT5, broker_symbol, override, info)

        path = str(_value(info, "path", "") or "").lower()
        description = str(_value(info, "description", "") or "").lower()
        searchable = f"{path} {description} {broker_symbol.lower()}"
        base = _clean_asset(_value(info, "currency_base", ""))
        quote = _clean_asset(
            _value(info, "currency_profit", "")
            or _value(info, "currency_margin", "")
        )

        group = AssetGroup.OTHER
        subgroup = "other"
        if self._is_forex(path, base, quote):
            group = AssetGroup.FOREX
            subgroup = "forex_major" if "USD" in {base, quote} else "forex_minor"
        elif self._is_metal(searchable, base):
            group = AssetGroup.METALS
            subgroup = "metal_precious"
        elif self._is_energy(searchable):
            group = AssetGroup.ENERGY
            subgroup = "energy_gas" if "gas" in searchable or "ngas" in searchable else "energy_oil"
        elif "commod" in path:
            group = AssetGroup.COMMODITIES_OTHER
            subgroup = "commodity_other"
        elif any(token in path for token in ("index", "indices")):
            group = AssetGroup.INDICES
            subgroup = self._index_subgroup(searchable)
        elif any(token in path for token in ("stock", "share", "equit")):
            group = AssetGroup.EQUITIES
            subgroup = "equity_us" if quote == "USD" or "nas" in searchable or "nyse" in searchable else "equity_other"

        canonical = f"{base}/{quote}" if base and quote and group in {AssetGroup.FOREX, AssetGroup.METALS} else broker_symbol.upper()
        metadata = self._mt5_metadata(info)
        return GhostInstrument(
            venue=Venue.MT5,
            broker_symbol=broker_symbol,
            canonical_symbol=canonical,
            asset_group=group,
            asset_subgroup=subgroup,
            base_asset=base,
            quote_asset=quote,
            metadata=metadata,
        )

    def classify_bybit(self, market: Mapping[str, Any]) -> GhostInstrument:
        broker_symbol = str(market.get("id") or market.get("symbol") or "").strip()
        override = self._overrides.get(broker_symbol)
        if override is not None:
            return self._from_override(Venue.BYBIT, broker_symbol, override, market)
        base = _clean_asset(market.get("base"))
        quote = _clean_asset(market.get("quote"))
        canonical = f"{base}/{quote}" if base and quote else str(market.get("symbol") or broker_symbol).split(":", 1)[0]
        subgroup = "crypto_major" if base in {"BTC", "ETH"} else "crypto_alt"
        return GhostInstrument(
            venue=Venue.BYBIT,
            broker_symbol=broker_symbol,
            canonical_symbol=canonical,
            asset_group=AssetGroup.CRYPTO,
            asset_subgroup=subgroup,
            base_asset=base,
            quote_asset=quote,
            metadata=dict(market),
        )

    def _from_override(
        self,
        venue: Venue,
        broker_symbol: str,
        override: Mapping[str, Any],
        source: Any,
    ) -> GhostInstrument:
        try:
            group = AssetGroup(str(override.get("asset_group", "other")).lower())
        except ValueError:
            group = AssetGroup.OTHER
        metadata = dict(source) if isinstance(source, Mapping) else self._mt5_metadata(source)
        return GhostInstrument(
            venue=venue,
            broker_symbol=broker_symbol,
            canonical_symbol=str(override.get("canonical_symbol") or broker_symbol),
            asset_group=group,
            asset_subgroup=str(override.get("asset_subgroup") or "other"),
            base_asset=_clean_asset(override.get("base_asset")),
            quote_asset=_clean_asset(override.get("quote_asset")),
            metadata=metadata,
        )

    @staticmethod
    def _is_forex(path: str, base: str, quote: str) -> bool:
        return "forex" in path or (base in _FOREX_CURRENCIES and quote in _FOREX_CURRENCIES)

    @staticmethod
    def _is_metal(searchable: str, base: str) -> bool:
        return "metal" in searchable or base in {"XAU", "XAG", "XPT", "XPD"}

    @staticmethod
    def _is_energy(searchable: str) -> bool:
        return any(token in searchable for token in ("energy", "oil", "wti", "brent", "ngas", "natural gas"))

    @staticmethod
    def _index_subgroup(searchable: str) -> str:
        if any(token in searchable for token in ("us", "nas", "dow", "sp500")):
            return "index_us"
        if any(token in searchable for token in ("ger", "dax", "uk", "euro", "fra")):
            return "index_europe"
        if any(token in searchable for token in ("jp", "hk", "china", "aus")):
            return "index_asia"
        return "index_other"

    @staticmethod
    def _mt5_metadata(info: Any) -> dict[str, Any]:
        keys = (
            "path", "description", "currency_base", "currency_profit",
            "currency_margin", "trade_mode", "visible", "expiration_time",
            "digits", "point", "trade_tick_size", "trade_tick_value",
            "trade_contract_size", "volume_min", "volume_max", "volume_step",
            "trade_stops_level", "trade_freeze_level",
        )
        return {key: _value(info, key) for key in keys if _value(info, key) is not None}
