import sys
import types

from scalp_engine import mt5_fetch_scalp_candles


class _FakeRateRow:
    def __init__(self, **fields):
        self._fields = fields

    def __getitem__(self, key):
        return self._fields[key]


def test_mt5_fetch_scalp_candles_accepts_structured_rows(monkeypatch):
    fake_mt5 = types.SimpleNamespace(
        TIMEFRAME_M5=5,
        TIMEFRAME_M15=15,
        terminal_info=lambda: True,
        initialize=lambda: True,
        symbol_select=lambda symbol, enabled: True,
        copy_rates_from_pos=lambda symbol, tf, start, count: [
            _FakeRateRow(time=1, open=100, high=101, low=99, close=100.5, tick_volume=10),
            _FakeRateRow(time=2, open=101, high=102, low=100, close=101.5, tick_volume=12),
        ],
    )

    monkeypatch.setitem(sys.modules, "MetaTrader5", fake_mt5)

    candles = mt5_fetch_scalp_candles("EURUSD", "M15", 2)

    assert candles == [
        {
            "time": 1,
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.5,
            "vol": 10.0,
        }
    ]
