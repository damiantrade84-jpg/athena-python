from eodhd_ticker_resolver import eodhd_ticker_for_pair, eodhd_ticker_from_signal


def test_commodity_display_maps_to_forex_ticker():
    assert eodhd_ticker_from_signal("XAU/USD", "commodity") == "XAUUSD.FOREX"


def test_forex_and_crypto_mappings():
    assert eodhd_ticker_from_signal("EUR/USD", "forex") == "EURUSD.FOREX"
    assert eodhd_ticker_from_signal("BTC/USDT", "crypto") == "BTC-USD.CC"


def test_legacy_commodity_symbol_mapping():
    assert eodhd_ticker_for_pair({"display": "Gold", "symbol": "GC=F", "type": "commodity"}) == "GC.COMEX"
