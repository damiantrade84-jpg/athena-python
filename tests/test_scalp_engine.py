import sys
import types
from datetime import datetime, timezone

import scalp_engine
from scalp_engine import (
    get_current_sessions,
    infer_bias_from_ema_stack,
    is_valid_session,
    mt5_get_live_price,
    mt5_fetch_scalp_candles,
)


class _FakeRateRow:
    def __init__(self, **fields):
        self._fields = fields

    def __getitem__(self, key):
        return self._fields[key]


def test_mt5_fetch_scalp_candles_accepts_structured_rows(monkeypatch):
    fake_mt5 = types.SimpleNamespace(
        TIMEFRAME_M5=5,
        TIMEFRAME_M15=15,
        TIMEFRAME_H1=60,
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


def test_mt5_fetch_scalp_candles_can_keep_forming_bar(monkeypatch):
    fake_mt5 = types.SimpleNamespace(
        TIMEFRAME_M5=5,
        TIMEFRAME_M15=15,
        TIMEFRAME_H1=60,
        terminal_info=lambda: True,
        initialize=lambda: True,
        symbol_select=lambda symbol, enabled: True,
        copy_rates_from_pos=lambda symbol, tf, start, count: [
            _FakeRateRow(time=1, open=100, high=101, low=99, close=100.5, tick_volume=10),
            _FakeRateRow(time=2, open=101, high=103, low=100, close=102.25, tick_volume=12),
        ],
    )

    monkeypatch.setitem(sys.modules, "MetaTrader5", fake_mt5)

    candles = mt5_fetch_scalp_candles("EURUSD", "M15", 2, include_forming=True)

    assert candles == [
        {
            "time": 1,
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.5,
            "vol": 10.0,
        },
        {
            "time": 2,
            "open": 101.0,
            "high": 103.0,
            "low": 100.0,
            "close": 102.25,
            "vol": 12.0,
        },
    ]


def test_mt5_get_live_price_prefers_bid_ask_mid(monkeypatch):
    fake_tick = types.SimpleNamespace(bid=1.2345, ask=1.2347, last=1.2346)
    fake_mt5 = types.SimpleNamespace(
        terminal_info=lambda: True,
        initialize=lambda: True,
        symbol_select=lambda symbol, enabled: True,
        symbol_info_tick=lambda symbol: fake_tick,
    )

    monkeypatch.setitem(sys.modules, "MetaTrader5", fake_mt5)

    assert mt5_get_live_price("EURUSD") == 1.2346


def test_infer_bias_from_ema_stack_detects_long_bias():
    candles = [{"close": float(i)} for i in range(1, 260)]

    assert infer_bias_from_ema_stack(candles) == "LONG"


def test_get_current_sessions_uses_timezone_aware_utc(monkeypatch):
    monkeypatch.setattr(
        scalp_engine,
        "_current_utc_datetime",
        lambda: datetime(2026, 3, 26, 13, 0, tzinfo=timezone.utc),
    )

    assert get_current_sessions() == ["london", "new_york"]


def test_is_valid_session_returns_off_hours_outside_utc_windows(monkeypatch):
    monkeypatch.setitem(
        scalp_engine.CONFIG,
        "SCALP_ENGINE",
        {
            **scalp_engine.CONFIG.get("SCALP_ENGINE", {}),
            "SESSION_FILTER": True,
        },
    )
    monkeypatch.setattr(
        scalp_engine,
        "_current_utc_datetime",
        lambda: datetime(2026, 3, 26, 22, 0, tzinfo=timezone.utc),
    )

    assert is_valid_session() == (False, "off_hours")


def test_run_scalp_scan_skips_counter_trend_signal(monkeypatch):
    import mt5_executor

    monkeypatch.setitem(
        scalp_engine.CONFIG,
        "SCALP_ENGINE",
        {
            **scalp_engine.CONFIG.get("SCALP_ENGINE", {}),
            "SESSION_FILTER": False,
            "WITH_TREND_ONLY": True,
            "BIAS_TIMEFRAME": "H1",
            "H1_CANDLES": 300,
            "M15_CANDLES": 50,
            "M5_CANDLES": 50,
        },
    )

    monkeypatch.setattr(mt5_executor, "mt5_connect", lambda: True)
    monkeypatch.setattr(mt5_executor, "mt5_map_symbol", lambda display: display.replace("/", ""))
    monkeypatch.setattr(
        mt5_executor,
        "mt5_get_symbol_info",
        lambda display: {"spread": 10, "point": 0.00001, "digits": 5},
    )

    def _fake_fetch(_symbol, timeframe, _count, include_forming=False):
        if timeframe == "H1":
            return [{"close": float(i)} for i in range(1, 260)]
        return [{"close": 1.1, "open": 1.1, "high": 1.2, "low": 1.0, "vol": 1, "time": i} for i in range(260)]

    monkeypatch.setattr(scalp_engine, "mt5_fetch_scalp_candles", _fake_fetch)
    monkeypatch.setattr(scalp_engine, "mt5_get_live_price", lambda _symbol: 1.1)
    monkeypatch.setattr(
        scalp_engine,
        "detect_m15_zone",
        lambda candles, current_price: {
            "valid": True,
            "zone_type": "resistance",
            "zone_high": current_price + 0.001,
            "zone_low": current_price - 0.001,
            "zone_level": current_price,
            "conditions_met": ["price_at_sr_level", "ema21_proximity"],
            "conditions_count": 2,
            "price_in_zone": True,
            "ema21": current_price,
        },
    )
    monkeypatch.setattr(
        scalp_engine,
        "detect_m5_trigger",
        lambda candles, zone, current_price: {
            "valid": True,
            "direction": "SHORT",
            "trigger_type": "rejection",
            "entry_price": current_price,
        },
    )
    monkeypatch.setattr(scalp_engine, "confirm_momentum", lambda candles, trigger: {"valid": True, "method": "structure_break"})
    monkeypatch.setattr(
        scalp_engine,
        "calculate_scalp_levels",
        lambda trigger, zone, candles, symbol_info, asset_type: {
            "entry": trigger["entry_price"],
            "sl": trigger["entry_price"] + 0.002,
            "tp1": trigger["entry_price"] - 0.003,
            "tp2": trigger["entry_price"] - 0.005,
            "rr": 1.5,
            "sl_distance": 0.002,
            "sl_method": "zone_boundary",
        },
    )
    monkeypatch.setattr(
        scalp_engine,
        "ai_quality_grade",
        lambda zone, trigger, momentum, sessions, spread_pips, symbol_info: {"score": 80, "grade": "A", "reasons": []},
    )

    result = scalp_engine.run_scalp_scan(["EUR/USD"])

    assert result["signals"] == []
    assert result["skipped"][0]["reason"] == "counter_trend:SHORT_vs_H1_LONG"


def test_run_scalp_scan_uses_live_tick_price(monkeypatch):
    import mt5_executor

    monkeypatch.setitem(
        scalp_engine.CONFIG,
        "SCALP_ENGINE",
        {
            **scalp_engine.CONFIG.get("SCALP_ENGINE", {}),
            "SESSION_FILTER": False,
            "WITH_TREND_ONLY": False,
            "M15_CANDLES": 50,
            "M5_CANDLES": 50,
        },
    )

    monkeypatch.setattr(mt5_executor, "mt5_connect", lambda: True)
    monkeypatch.setattr(mt5_executor, "mt5_map_symbol", lambda display: display.replace("/", ""))
    monkeypatch.setattr(
        mt5_executor,
        "mt5_get_symbol_info",
        lambda display: {"spread": 10, "point": 0.00001, "digits": 5},
    )

    fetch_calls = []

    def _fake_fetch(_symbol, timeframe, _count, include_forming=False):
        fetch_calls.append((timeframe, include_forming))
        return [
            {"close": 1.1, "open": 1.1, "high": 1.2, "low": 1.0, "vol": 1, "time": i}
            for i in range(60)
        ]

    monkeypatch.setattr(scalp_engine, "mt5_fetch_scalp_candles", _fake_fetch)
    monkeypatch.setattr(scalp_engine, "mt5_get_live_price", lambda _symbol: 1.23456)
    monkeypatch.setattr(
        scalp_engine,
        "detect_m15_zone",
        lambda candles, current_price: {
            "valid": True,
            "zone_type": "support",
            "zone_high": current_price + 0.001,
            "zone_low": current_price - 0.001,
            "zone_level": current_price,
            "conditions_met": ["price_at_sr_level", "ema21_proximity"],
            "conditions_count": 2,
            "price_in_zone": True,
            "ema21": current_price,
        },
    )
    monkeypatch.setattr(
        scalp_engine,
        "detect_m5_trigger",
        lambda candles, zone, current_price: {
            "valid": True,
            "direction": "LONG",
            "trigger_type": "rejection",
            "entry_price": current_price,
        },
    )
    monkeypatch.setattr(
        scalp_engine,
        "confirm_momentum",
        lambda candles, trigger: {"valid": True, "method": "structure_break"},
    )
    monkeypatch.setattr(
        scalp_engine,
        "calculate_scalp_levels",
        lambda trigger, zone, candles, symbol_info, asset_type: {
            "entry": trigger["entry_price"],
            "sl": trigger["entry_price"] - 0.002,
            "tp1": trigger["entry_price"] + 0.003,
            "tp2": trigger["entry_price"] + 0.005,
            "rr": 1.5,
            "sl_distance": 0.002,
            "sl_method": "zone_boundary",
        },
    )
    monkeypatch.setattr(
        scalp_engine,
        "ai_quality_grade",
        lambda zone, trigger, momentum, sessions, spread_pips, symbol_info: {"score": 80, "grade": "A", "reasons": []},
    )

    result = scalp_engine.run_scalp_scan(["EUR/USD"])

    assert result["signals"]
    assert result["signals"][0]["price"] == 1.23456
    assert ("M15", True) in fetch_calls
    assert ("M5", True) in fetch_calls
