"""Carry signal — swing horizon only (ASE v2.1 §3.2)."""

from __future__ import annotations

import math
from dataclasses import dataclass

from athena_ase.data.ingest.common import bybit_series_id, fred_series_id
from athena_ase.data.ptis import PTISStore
from athena_ase.instruments import Instrument, compact_symbol

# Copied from carry_feed.py — FRED short-rate series per currency (no legacy import).
FRED_CURRENCY_SERIES: dict[str, str] = {
    "USD": "DFF",
    "EUR": "ECBDFR",
    "GBP": "IRSTCI01GBM156N",
    "JPY": "IRSTCI01JPM156N",
    "AUD": "IRSTCI01AUM156N",
    "CHF": "IRSTCI01CHM156N",
    "ZAR": "IRSTCI01ZAM156N",
}

# Forex pair → (weight, currency) legs for rate differential.
FOREX_CARRY_LEGS: dict[str, list[tuple[float, str]]] = {
    "EURUSD": [(1.0, "EUR"), (-1.0, "USD")],
    "GBPUSD": [(1.0, "GBP"), (-1.0, "USD")],
    "USDJPY": [(1.0, "USD"), (-1.0, "JPY")],
    "AUDUSD": [(1.0, "AUD"), (-1.0, "USD")],
    "NZDUSD": [(1.0, "NZD"), (-1.0, "USD")],
    "EURGBP": [(1.0, "EUR"), (-1.0, "GBP")],
    "USDCAD": [(1.0, "USD"), (-1.0, "CAD")],
    "USDCHF": [(1.0, "USD"), (-1.0, "CHF")],
    "EURJPY": [(1.0, "EUR"), (-1.0, "JPY")],
    "GBPJPY": [(1.0, "GBP"), (-1.0, "JPY")],
    "AUDJPY": [(1.0, "AUD"), (-1.0, "JPY")],
    "EURAUD": [(1.0, "EUR"), (-1.0, "AUD")],
    "GBPAUD": [(1.0, "GBP"), (-1.0, "AUD")],
    "EURCHF": [(1.0, "EUR"), (-1.0, "CHF")],
    "USDMXN": [(1.0, "USD"), (-1.0, "MXN")],
    "USDZAR": [(1.0, "USD"), (-1.0, "ZAR")],
    "USDSGD": [(1.0, "USD"), (-1.0, "SGD")],
}


@dataclass(frozen=True)
class CarryResult:
    direction: int
    raw_strength: float
    carry_annual: float
    cvr: float


def _fred_rate_at(store: PTISStore, currency: str, decision_time_ms: int) -> float | None:
    sid = FRED_CURRENCY_SERIES.get(currency.upper())
    if not sid:
        return None
    series_id = fred_series_id(sid)
    try:
        rows = store.asof(series_id, decision_time_ms, n=1)
    except KeyError:
        return None
    if len(rows) == 0:
        return None
    return float(rows[0]["value"])


def _forex_carry_annual(store: PTISStore, instrument: Instrument, decision_time_ms: int) -> float | None:
    legs = FOREX_CARRY_LEGS.get(compact_symbol(instrument.symbol))
    if not legs:
        return None
    total = 0.0
    for weight, ccy in legs:
        rate = _fred_rate_at(store, ccy, decision_time_ms)
        if rate is None:
            return None
        total += weight * rate
    return total


def _crypto_funding_annual(store: PTISStore, symbol: str, decision_time_ms: int) -> float | None:
    sid = bybit_series_id(compact_symbol(symbol), "funding")
    try:
        rows = store.asof(sid, decision_time_ms, n=1)
    except KeyError:
        return None
    if len(rows) == 0:
        return None
    # Bybit funding rate per 8h → annualized (3× per day × 365)
    rate = float(rows[0]["value"])
    return rate * 3.0 * 365.0


def compute_carry(
    store: PTISStore,
    instrument: Instrument,
    decision_time_ms: int,
    sigma_1bar: float,
) -> CarryResult:
    if math.isnan(sigma_1bar) or sigma_1bar <= 0:
        return CarryResult(0, 0.0, 0.0, 0.0)

    carry: float | None = None
    if instrument.family == "forex":
        carry = _forex_carry_annual(store, instrument, decision_time_ms)
    elif instrument.family == "crypto":
        carry = _crypto_funding_annual(store, instrument.symbol, decision_time_ms)
    else:
        return CarryResult(0, 0.0, 0.0, 0.0)

    if carry is None:
        return CarryResult(0, 0.0, 0.0, 0.0)

    cvr = carry / (sigma_1bar * math.sqrt(252.0))
    cvr = max(-2.0, min(2.0, cvr))
    if abs(cvr) < 0.4:
        return CarryResult(0, 0.0, carry, cvr)
    direction = 1 if carry > 0 else -1
    raw_strength = min(abs(cvr) / 2.0, 1.0)
    return CarryResult(direction, raw_strength, carry, cvr)
