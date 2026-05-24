from symbol_matching import symbols_match


def test_symbols_match_slash_compact_yahoo_and_provider_forms():
    assert symbols_match("EUR/USD", "EURUSD=X")
    assert symbols_match("BTC/USDT", "BTCUSDT")
    assert symbols_match("BINANCE:BTCUSDT", "BTC/USDT")
    assert symbols_match("BTC/USDT:USDT", "BTC/USDT")


def test_symbols_match_does_not_match_shared_settlement_suffix():
    assert not symbols_match("ETH/USDT:USDT", "BTC/USDT:USDT")
    assert not symbols_match("BINANCE:ETHUSDT", "BTC/USDT")
    assert not symbols_match("BINANCE:ETHUSDT", "BINANCE:BTCUSDT")
