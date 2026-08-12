"""Commodity catalog vs execution identity (SI=F futures ≠ XAG/USD spot)."""

from __future__ import annotations

from athena_app.services.commodity_identity import (
    commodity_instrument_identity,
    is_continuous_futures_ticker,
    resolve_yfinance_pricing_symbol,
)


def test_is_continuous_futures_ticker():
    assert is_continuous_futures_ticker("SI=F") is True
    assert is_continuous_futures_ticker("GC=F") is True
    assert is_continuous_futures_ticker("XAGUSD") is False
    assert is_continuous_futures_ticker("XAG/USD") is False


def test_yfinance_blocked_for_mt5_commodity_continuous_futures_catalog():
    """XAG/USD is MT5 spot CFD; SI=F must not become a silent pricing series."""
    pair = {
        "symbol": "SI=F",
        "display": "XAG/USD",
        "type": "commodity",
        "source": "mt5",
        "futures_proxy": "SI=F",
        "price_series_kind": "spot_cfd",
    }
    assert resolve_yfinance_pricing_symbol(pair) is None


def test_yfinance_blocked_for_xau_mt5_gc_futures_catalog():
    pair = {
        "symbol": "GC=F",
        "display": "XAU/USD",
        "type": "commodity",
        "source": "mt5",
    }
    assert resolve_yfinance_pricing_symbol(pair) is None


def test_yfinance_explicit_override_still_allowed():
    """Opt-in only — research/debug may force a yfinance ticker."""
    pair = {
        "symbol": "SI=F",
        "display": "XAG/USD",
        "type": "commodity",
        "source": "mt5",
        "yfinanceSymbol": "SI=F",
    }
    assert resolve_yfinance_pricing_symbol(pair) == "SI=F"


def test_yfinance_empty_override_blocks():
    pair = {
        "symbol": "SI=F",
        "display": "XAG/USD",
        "type": "commodity",
        "source": "mt5",
        "yfinanceSymbol": "",
    }
    assert resolve_yfinance_pricing_symbol(pair) is None


def test_yfinance_still_returns_symbol_for_non_mt5_pairs():
    pair = {"symbol": "SI=F", "display": "XAG/USD", "type": "commodity", "source": "yfinance"}
    assert resolve_yfinance_pricing_symbol(pair) == "SI=F"


def test_commodity_instrument_identity_separates_futures_proxy_from_execution():
    identity = commodity_instrument_identity(
        {
            "symbol": "SI=F",
            "display": "XAG/USD",
            "type": "commodity",
            "source": "mt5",
            "futures_proxy": "SI=F",
            "price_series_kind": "spot_cfd",
        },
        mt5_map_symbol=lambda _d: "XAGUSD",
    )
    assert identity["catalog_symbol"] == "SI=F"
    assert identity["display"] == "XAG/USD"
    assert identity["futures_proxy"] == "SI=F"
    assert identity["execution_symbol"] == "XAGUSD"
    assert identity["price_series_kind"] == "spot_cfd"
    assert identity["catalog_is_continuous_futures"] is True
    assert identity["note"] and "SI=F" in identity["note"] and "XAGUSD" in identity["note"]
