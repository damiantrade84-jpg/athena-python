"""OPUS universe now follows Athena's active portfolio."""

from __future__ import annotations

import pytest

from opus import config, universe
from opus.risk.sizing import correlation_group_of


PORTFOLIO = [
    {"symbol": "EURUSD=X", "type": "forex", "display": "EUR/USD", "source": "mt5", "enabled": True},
    {"symbol": "GC=F", "type": "commodity", "display": "XAU/USD", "source": "mt5", "enabled": True},
    {"symbol": "BRENT", "type": "commodity", "display": "Brent Oil", "source": "mt5", "enabled": True},
    {"symbol": "NAS100", "type": "index", "display": "NASDAQ-100", "source": "mt5", "enabled": True},
    {"symbol": "AAPL.US", "type": "stock", "display": "AAPL", "source": "mt5", "enabled": True},
    {"symbol": "BTCUSDT", "type": "crypto", "display": "BTC/USDT", "source": "binance", "enabled": True},
    {"symbol": "GBPUSD=X", "type": "forex", "display": "GBP/USD", "source": "mt5", "enabled": False},
    {"symbol": "NPN.JO", "type": "stock", "display": "Naspers", "source": "eodhd", "enabled": True},
]


@pytest.fixture(autouse=True)
def _clean_provider():
    universe.set_portfolio_provider(None)
    yield
    universe.set_portfolio_provider(None)


def _by_display(specs):
    return {s["display"]: s for s in specs}


def test_disabled_pairs_never_reach_the_universe():
    specs, _ = universe.build_specs(PORTFOLIO)
    assert "GBP/USD" not in _by_display(specs)


def test_asset_class_mapping_splits_metals_from_other_commodities():
    specs, _ = universe.build_specs(PORTFOLIO)
    rows = _by_display(specs)
    assert rows["XAU/USD"]["class"] == "metals"
    assert rows["Brent Oil"]["class"] == "commodities"
    assert rows["NASDAQ-100"]["class"] == "indices"
    assert rows["AAPL"]["class"] == "equities"
    assert rows["EUR/USD"]["class"] == "forex"
    assert rows["BTC/USDT"]["class"] == "crypto"


def test_every_derived_class_has_a_timeframe_ladder_and_cost_model():
    specs, _ = universe.build_specs(PORTFOLIO)
    for spec in specs:
        tfs = config.timeframes_for(spec["class"])
        assert set(tfs) == {"context", "structure", "trigger"}
        costs = config.cost_model(spec["class"])
        assert costs["commission_bps"] >= 0.0


def test_source_without_an_opus_adapter_is_skipped_with_a_reason():
    specs, skipped = universe.build_specs(PORTFOLIO)
    assert "Naspers" not in _by_display(specs)
    assert any(row["display"] == "Naspers" and "eodhd" in row["reason"] for row in skipped)


def test_crypto_routes_to_bybit_not_the_portfolio_source():
    specs, _ = universe.build_specs(PORTFOLIO)
    assert _by_display(specs)["BTC/USDT"]["venue"] == "bybit"


def test_broker_symbol_comes_from_the_resolver_not_the_catalog_id():
    """`pair['symbol']` is a Yahoo/EODHD id and must never reach a feed."""
    universe.set_portfolio_provider(
        lambda: PORTFOLIO,
        symbol_resolver=lambda display: {"NASDAQ-100": "NAS100", "XAU/USD": "XAUUSD"}.get(display),
    )
    specs, _ = universe.portfolio_specs()
    rows = _by_display(specs)
    assert rows["NASDAQ-100"]["symbol"] == "NAS100"
    assert rows["XAU/USD"]["symbol"] == "XAUUSD"
    # No mapping for this one: the stripped display is the fallback, never GC=F.
    assert rows["EUR/USD"]["symbol"] == "EURUSD"
    assert not any(s["symbol"].endswith("=X") or s["symbol"].endswith(".US") for s in specs)


def test_config_universe_uses_the_portfolio_when_a_provider_is_installed():
    universe.set_portfolio_provider(lambda: PORTFOLIO)
    resolved = config.universe()
    assert {s["display"] for s in resolved} == {
        "EUR/USD", "XAU/USD", "Brent Oil", "NASDAQ-100", "AAPL", "BTC/USDT",
    }
    source = config.universe_source()
    assert source["resolved"] == "portfolio"
    assert source["count"] == 6


def test_config_universe_falls_back_to_the_static_list_without_a_provider():
    """No runtime attached must mean the shipped list, never an empty universe."""
    resolved = config.universe()
    assert resolved
    assert resolved == [dict(e) for e in config.load()["UNIVERSE"]]
    assert config.universe_source()["resolved"] == "config"


def test_a_raising_provider_falls_back_instead_of_emptying_the_universe():
    def _boom():
        raise RuntimeError("portfolio unavailable")

    universe.set_portfolio_provider(_boom)
    assert config.universe() == [dict(e) for e in config.load()["UNIVERSE"]]


def test_toggling_a_pair_off_takes_effect_without_a_restart():
    state = [dict(p) for p in PORTFOLIO]
    universe.set_portfolio_provider(lambda: state)
    assert "EUR/USD" in _by_display(config.universe())
    for pair in state:
        if pair["display"] == "EUR/USD":
            pair["enabled"] = False
    # The memo is keyed on (display, enabled), so a toggle invalidates it.
    assert "EUR/USD" not in _by_display(config.universe())


def test_symbols_outside_a_named_group_are_capped_by_their_asset_class():
    """The old behaviour left every unlisted symbol with no correlation cap."""
    universe.set_portfolio_provider(lambda: PORTFOLIO)
    # Explicitly grouped symbols keep their group.
    assert correlation_group_of("EURUSD") == "USD_MAJORS"
    # An unlisted one now falls back to its class rather than returning None.
    assert correlation_group_of("AAPL") == "CLASS_EQUITIES"
    index_group = correlation_group_of("NASDAQ100")
    assert index_group is None or index_group.startswith("CLASS_")
