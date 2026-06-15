from __future__ import annotations

from dataclasses import dataclass
import math


FOREX_PAIRS: tuple[str, ...] = (
    "EURUSD",
    "GBPUSD",
    "USDJPY",
    "AUDUSD",
    "AUDCHF",
    "AUDNZD",
    "NZDUSD",
    "EURGBP",
    "USDCAD",
    "USDCHF",
    "EURJPY",
    "GBPJPY",
    "AUDJPY",
    "EURAUD",
    "GBPAUD",
    "USDZAR",
    "EURCHF",
    "USDMXN",
    "USDSGD",
    "USDBRL",
    "USDINR",
)

CURRENCIES: tuple[str, ...] = (
    "USD",
    "EUR",
    "GBP",
    "JPY",
    "AUD",
    "CHF",
    "NZD",
    "CAD",
    "ZAR",
    "MXN",
    "SGD",
    "BRL",
    "INR",
)


@dataclass(frozen=True)
class CanonicalPair:
    pair: str
    currency: str
    usd_per_currency: bool


CANONICAL_USD_PAIRS: dict[str, CanonicalPair] = {
    "EUR": CanonicalPair("EURUSD", "EUR", True),
    "GBP": CanonicalPair("GBPUSD", "GBP", True),
    "JPY": CanonicalPair("USDJPY", "JPY", False),
    "AUD": CanonicalPair("AUDUSD", "AUD", True),
    "CHF": CanonicalPair("USDCHF", "CHF", False),
    "NZD": CanonicalPair("NZDUSD", "NZD", True),
    "CAD": CanonicalPair("USDCAD", "CAD", False),
    "ZAR": CanonicalPair("USDZAR", "ZAR", False),
    "MXN": CanonicalPair("USDMXN", "MXN", False),
    "SGD": CanonicalPair("USDSGD", "SGD", False),
    "BRL": CanonicalPair("USDBRL", "BRL", False),
    "INR": CanonicalPair("USDINR", "INR", False),
}


def currency_usd_price(currency: str, pair_value: float) -> float:
    ccy = currency.upper()
    value = float(pair_value)
    if not math.isfinite(value):
        raise ValueError("pair_value must be finite")
    if value <= 0:
        raise ValueError("pair_value must be positive")
    if ccy == "USD":
        return 1.0
    spec = CANONICAL_USD_PAIRS[ccy]
    return value if spec.usd_per_currency else 1.0 / value


def pair_weight_for_currency(currency: str, currency_weight: float) -> float:
    spec = CANONICAL_USD_PAIRS[currency.upper()]
    weight = float(currency_weight)
    if not math.isfinite(weight):
        raise ValueError("currency_weight must be finite")
    return weight if spec.usd_per_currency else -weight
