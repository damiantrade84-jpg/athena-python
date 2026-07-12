from __future__ import annotations

from types import SimpleNamespace

from ghost_trade.models import AssetGroup, Venue
from ghost_trade.symbols import CanonicalSymbolService
from ghost_trade.universe import BybitUniverseProvider, MT5UniverseProvider


def test_mt5_classification_uses_metadata_and_preserves_exact_symbol():
    service = CanonicalSymbolService()
    info = SimpleNamespace(
        name="EURUSD.a",
        path="Forex\\Majors",
        description="Euro vs US Dollar",
        currency_base="EUR",
        currency_profit="USD",
        trade_mode=4,
        visible=True,
    )

    instrument = service.classify_mt5(info)

    assert instrument.venue is Venue.MT5
    assert instrument.broker_symbol == "EURUSD.a"
    assert instrument.canonical_symbol == "EUR/USD"
    assert instrument.asset_group is AssetGroup.FOREX
    assert instrument.asset_subgroup == "forex_major"


def test_configured_override_wins_for_broker_specific_symbol():
    service = CanonicalSymbolService(
        {
            "US500.cash": {
                "canonical_symbol": "S&P 500",
                "asset_group": "indices",
                "asset_subgroup": "index_us",
                "base_asset": "SPX",
                "quote_asset": "USD",
            }
        }
    )
    info = SimpleNamespace(
        name="US500.cash",
        path="CFD\\Unknown",
        description="Broker CFD",
        trade_mode=4,
        visible=True,
    )

    instrument = service.classify_mt5(info)

    assert instrument.canonical_symbol == "S&P 500"
    assert instrument.asset_group is AssetGroup.INDICES
    assert instrument.asset_subgroup == "index_us"


def test_unknown_mt5_instrument_remains_visible_as_other():
    instrument = CanonicalSymbolService().classify_mt5(
        SimpleNamespace(
            name="BROKER.X1",
            path="Custom",
            description="Unclassified contract",
            trade_mode=4,
            visible=True,
        )
    )

    assert instrument.asset_group is AssetGroup.OTHER
    assert instrument.canonical_symbol == "BROKER.X1"


class FakeMT5:
    SYMBOL_TRADE_MODE_DISABLED = 0

    def __init__(self, symbols, selectable=()):
        self._symbols = symbols
        self._selectable = set(selectable)
        self.select_calls = []

    def symbols_get(self):
        return self._symbols

    def symbol_select(self, symbol, enabled):
        self.select_calls.append((symbol, enabled))
        return symbol in self._selectable


def _mt5_info(name, *, visible=True, trade_mode=4, path="Forex", base="EUR", quote="USD"):
    return SimpleNamespace(
        name=name,
        visible=visible,
        trade_mode=trade_mode,
        path=path,
        description=name,
        currency_base=base,
        currency_profit=quote,
        expiration_time=0,
    )


def test_mt5_universe_is_deterministic_and_retains_skip_reasons():
    client = FakeMT5(
        [
            _mt5_info("ZZZ", visible=False, path="Custom", base="", quote=""),
            _mt5_info("GBPJPY-pro", base="GBP", quote="JPY"),
            _mt5_info("EURUSD.a", base="EUR", quote="USD"),
            _mt5_info("DISABLED", trade_mode=0, path="Custom", base="", quote=""),
        ],
        selectable={"ZZZ"},
    )
    provider = MT5UniverseProvider(
        client,
        CanonicalSymbolService(),
        history_checker=lambda symbol: symbol != "GBPJPY-pro",
    )

    result = provider.discover()

    assert [item.broker_symbol for item in result.instruments] == [
        "DISABLED",
        "EURUSD.a",
        "GBPJPY-pro",
        "ZZZ",
    ]
    by_symbol = {item.broker_symbol: item for item in result.instruments}
    assert by_symbol["DISABLED"].skip_reasons == ("trade_disabled",)
    assert by_symbol["GBPJPY-pro"].skip_reasons == ("insufficient_history",)
    assert by_symbol["ZZZ"].skip_reasons == ()
    assert result.discovered_count == 4
    assert result.eligible_count == 2


class FakeBybit:
    def __init__(self, markets):
        self._markets = markets
        self.load_calls = 0

    def load_markets(self):
        self.load_calls += 1
        return self._markets


def _market(symbol, **overrides):
    market = {
        "id": symbol.replace("/", "").replace(":USDT", ""),
        "symbol": symbol,
        "base": symbol.split("/")[0],
        "quote": "USDT",
        "active": True,
        "swap": True,
        "linear": True,
        "spot": False,
        "precision": {"price": 0.01, "amount": 0.001},
        "limits": {"amount": {"min": 0.001}, "cost": {"min": 5}},
        "info": {"status": "Trading", "launchTime": "1"},
    }
    market.update(overrides)
    return market


def test_bybit_universe_filters_inactive_spot_filters_and_history_without_hiding_rows():
    markets = {
        "ETH/USDT:USDT": _market("ETH/USDT:USDT"),
        "BTC/USDT:USDT": _market("BTC/USDT:USDT"),
        "DOGE/USDT": _market("DOGE/USDT", swap=False, linear=False, spot=True),
        "OLD/USDT:USDT": _market("OLD/USDT:USDT", active=False),
        "NOFILTER/USDT:USDT": _market("NOFILTER/USDT:USDT", precision={}),
    }
    provider = BybitUniverseProvider(
        FakeBybit(markets),
        CanonicalSymbolService(),
        allow_spot=False,
        history_checker=lambda symbol: not symbol.startswith("ETH"),
    )

    result = provider.discover()

    assert [item.canonical_symbol for item in result.instruments] == [
        "BTC/USDT",
        "DOGE/USDT",
        "ETH/USDT",
        "NOFILTER/USDT",
        "OLD/USDT",
    ]
    by_symbol = {item.canonical_symbol: item for item in result.instruments}
    assert by_symbol["BTC/USDT"].skip_reasons == ()
    assert by_symbol["DOGE/USDT"].skip_reasons == ("category_not_enabled",)
    assert by_symbol["ETH/USDT"].skip_reasons == ("insufficient_history",)
    assert by_symbol["NOFILTER/USDT"].skip_reasons == (
        "missing_price_filter",
        "missing_quantity_filter",
    )
    assert by_symbol["OLD/USDT"].skip_reasons == ("inactive",)
    assert result.group_counts == {"crypto": 5}
    assert result.eligible_count == 1


def test_provider_failure_is_explicit_instead_of_an_empty_success():
    class BrokenBybit:
        def load_markets(self):
            raise RuntimeError("provider offline")

    result = BybitUniverseProvider(
        BrokenBybit(), CanonicalSymbolService()
    ).discover()

    assert result.instruments == ()
    assert result.errors == ("bybit_discovery_failed:provider offline",)


def test_one_malformed_instrument_does_not_hide_other_discovered_rows():
    client = FakeMT5(
        [SimpleNamespace(name="", visible=True, trade_mode=4), _mt5_info("EURUSD")]
    )

    result = MT5UniverseProvider(client, CanonicalSymbolService()).discover()

    assert [item.broker_symbol for item in result.instruments] == ["EURUSD"]
    assert len(result.errors) == 1
    assert result.errors[0].startswith("mt5_instrument_failed:")
