from candle_feeds import resolve_binance_kline_ws_intervals
from config import CONFIG


def test_default_binance_kline_ws_intervals_include_h4_and_d1():
    intervals = resolve_binance_kline_ws_intervals({})

    assert intervals == ["1m", "5m", "15m", "1h", "4h", "1d"]


def test_config_binance_kline_ws_intervals_include_h4_and_d1():
    intervals = resolve_binance_kline_ws_intervals(CONFIG)

    assert "4h" in intervals
    assert "1d" in intervals


def test_binance_kline_ws_interval_config_filters_unsupported_values():
    intervals = resolve_binance_kline_ws_intervals(
        {"BINANCE_KLINE_WS_INTERVALS": ["1m", "2h", "4h", "4h", "1d"]}
    )

    assert intervals == ["1m", "4h", "1d"]

