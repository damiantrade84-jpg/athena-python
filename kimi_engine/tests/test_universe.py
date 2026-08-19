"""Athena active-book → KIMI universe names."""
from __future__ import annotations

from kimi.universe import (
    canonicalize_kimi_symbol,
    pair_to_kimi_symbol,
    portfolio_symbols,
)


def test_canonicalize_strips_broker_suffix_and_share_prefix():
    assert canonicalize_kimi_symbol("EURUSD.s") == "EURUSD"
    assert canonicalize_kimi_symbol("eurusd.s") == "EURUSD"
    assert canonicalize_kimi_symbol("#AAPL") == "AAPL"
    assert canonicalize_kimi_symbol("AAPL.US") == "AAPL"
    assert canonicalize_kimi_symbol("BTC/USDT") == "BTCUSDT"
    assert canonicalize_kimi_symbol("WTI.s") == "WTI"
    assert canonicalize_kimi_symbol("GC=F") is None
    assert canonicalize_kimi_symbol("#ETF-EEM") is None
    assert canonicalize_kimi_symbol("C") is None
    assert canonicalize_kimi_symbol("") is None


def test_atfx_mapped_fx_is_kept_not_dropped():
    """Regression: EURUSD.s used to fail [A-Z0-9] and vanish from ALL ACTIVE."""
    pair = {"display": "EUR/USD", "symbol": "EURUSD=X", "type": "forex",
            "source": "mt5", "enabled": True}
    assert pair_to_kimi_symbol(pair, lambda d: "EURUSD.s") == "EURUSD"


def test_share_cfd_uses_stripped_ticker():
    pair = {"display": "AAPL", "symbol": "AAPL.US", "type": "stock",
            "source": "mt5", "enabled": True}
    assert pair_to_kimi_symbol(pair, lambda d: "#AAPL") == "AAPL"


def test_meta_maps_through_broker_alias():
    pair = {"display": "META", "symbol": "META.US", "type": "stock",
            "source": "mt5", "enabled": True}
    assert pair_to_kimi_symbol(pair, lambda d: "#FB") == "FB"


def test_crypto_display_without_mapper():
    pair = {"display": "BTC/USDT", "symbol": "BTCUSDT", "type": "crypto",
            "source": "binance", "enabled": True}
    assert pair_to_kimi_symbol(pair, None) == "BTCUSDT"


def test_disabled_pairs_are_skipped():
    pair = {"display": "EUR/USD", "symbol": "EURUSD=X", "enabled": False}
    assert pair_to_kimi_symbol(pair, lambda d: "EURUSD.s") is None


def test_portfolio_is_active_book_not_catalog():
    pairs = [
        {"display": "EUR/USD", "symbol": "EURUSD=X", "enabled": True},
        {"display": "GBP/USD", "symbol": "GBPUSD=X", "enabled": True},
        {"display": "AAPL", "symbol": "AAPL.US", "enabled": True},
        {"display": "SKIP", "symbol": "SKIP", "enabled": False},
        {"display": "EEM", "symbol": "EEM.US", "enabled": True},
    ]
    mapping = {
        "EUR/USD": "EURUSD.s",
        "GBP/USD": "GBPUSD.s",
        "AAPL": "#AAPL",
        "EEM": "#ETF-EEM",
    }
    out = portfolio_symbols(pairs, mapping.get)
    assert out == ["EURUSD", "GBPUSD", "AAPL", "EEM"]
    assert "EURUSD.S" not in out
    assert len(out) == 4


def test_set_symbols_accepts_canonicalized_three_letter_ticker(tmp_path):
    from kimi.engine import Engine
    eng = Engine()
    eng.paper.state_path = str(tmp_path / "st.json")
    r = eng.set_symbols(["WTI", "eurusd.s", "#AAPL", "C", "BTCUSDT"])
    assert r["ok"]
    assert r["symbols"] == ["WTI", "EURUSD", "AAPL", "BTCUSDT"]
