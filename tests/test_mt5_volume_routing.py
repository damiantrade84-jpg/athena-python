import os
import sys
import types

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import scalp_engine
from eodhd_volume_overlay import eodhd_live_overlay_asset_types


def _candles(n: int, vol: float = 100.0) -> list[dict]:
    return [
        {
            "time": f"2026-03-31T08:{i:02d}:00+00:00",
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.5,
            "vol": vol,
        }
        for i in range(n)
    ]


class _FakeRateRow:
    def __init__(self, **fields):
        self._fields = fields

    def __getitem__(self, key):
        return self._fields[key]


def test_live_overlay_asset_types_default_is_stock_only(monkeypatch):
    monkeypatch.setitem(
        scalp_engine.CONFIG,
        "SCALP_ENGINE",
        {k: v for k, v in scalp_engine.CONFIG.get("SCALP_ENGINE", {}).items()
         if k != "EODHD_VOLUME_OVERLAY_LIVE_ASSET_TYPES"},
    )
    assert eodhd_live_overlay_asset_types() == {"stock"}


def test_live_overlay_asset_types_reads_scalp_engine_config(monkeypatch):
    monkeypatch.setitem(
        scalp_engine.CONFIG,
        "SCALP_ENGINE",
        {
            **scalp_engine.CONFIG.get("SCALP_ENGINE", {}),
            "EODHD_VOLUME_OVERLAY_LIVE_ASSET_TYPES": ["stock", "commodity"],
        },
    )
    assert eodhd_live_overlay_asset_types() == {"stock", "commodity"}


def test_commodity_live_skips_eodhd_overlay_in_scalp(monkeypatch):
    calls = []

    class _FakeRt:
        def fetch_eodhd_volume_only(self, *args, **kwargs):
            calls.append((args, kwargs))
            raise AssertionError("EODHD fetch should not run for live commodity")

    monkeypatch.setitem(
        scalp_engine.CONFIG,
        "SCALP_ENGINE",
        {
            **scalp_engine.CONFIG.get("SCALP_ENGINE", {}),
            "EODHD_VOLUME_OVERLAY_LIVE_ENABLED": True,
            "EODHD_VOLUME_OVERLAY_LIVE_ASSET_TYPES": ["stock"],
        },
    )
    monkeypatch.setitem(sys.modules, "athena_runtime", types.SimpleNamespace(rt=lambda: _FakeRt()))

    base = _candles(3, vol=42.0)
    out, source = scalp_engine._overlay_eodhd_volume_for_scalp(
        "XAU/USD", "commodity", "M15", list(base), live=True
    )

    assert out == base
    assert source == "mt5_tick"
    assert calls == []


def test_index_live_skips_eodhd_overlay_in_scalp(monkeypatch):
    calls = []

    class _FakeRt:
        def fetch_eodhd_volume_only(self, *args, **kwargs):
            calls.append((args, kwargs))
            raise AssertionError("EODHD fetch should not run for live index")

    monkeypatch.setitem(
        scalp_engine.CONFIG,
        "SCALP_ENGINE",
        {
            **scalp_engine.CONFIG.get("SCALP_ENGINE", {}),
            "EODHD_VOLUME_OVERLAY_LIVE_ENABLED": True,
            "EODHD_VOLUME_OVERLAY_LIVE_ASSET_TYPES": ["stock"],
        },
    )
    monkeypatch.setitem(sys.modules, "athena_runtime", types.SimpleNamespace(rt=lambda: _FakeRt()))

    base = _candles(3, vol=55.0)
    out, source = scalp_engine._overlay_eodhd_volume_for_scalp(
        "S&P 500", "index", "M15", list(base), live=True
    )

    assert out == base
    assert source == "mt5_tick"
    assert calls == []


def test_stock_live_still_uses_eodhd_overlay_in_scalp(monkeypatch):
    calls = []

    class _FakeRt:
        def fetch_eodhd_volume_only(self, pair, tf, limit, *, cache_only=True, grid_offset_seconds=0):
            calls.append((pair, tf, limit, cache_only))
            return {
                "volume_source": "ws_tick",
                "candles": [
                    {
                        "time": "2026-03-31T08:00:00+00:00",
                        "open": 1,
                        "high": 1,
                        "low": 1,
                        "close": 1,
                        "vol": 9999.0,
                    },
                    {
                        "time": "2026-03-31T08:01:00+00:00",
                        "open": 1,
                        "high": 1,
                        "low": 1,
                        "close": 1,
                        "vol": 8888.0,
                    },
                    {
                        "time": "2026-03-31T08:02:00+00:00",
                        "open": 1,
                        "high": 1,
                        "low": 1,
                        "close": 1,
                        "vol": 7777.0,
                    },
                ],
            }

    monkeypatch.setitem(
        scalp_engine.CONFIG,
        "SCALP_ENGINE",
        {
            **scalp_engine.CONFIG.get("SCALP_ENGINE", {}),
            "EODHD_VOLUME_OVERLAY_LIVE_ENABLED": True,
            "EODHD_VOLUME_OVERLAY_LIVE_ASSET_TYPES": ["stock"],
            "EODHD_STOCK_EXCHANGE_SUFFIX_MAP": {"AAPL": ".US"},
        },
    )
    monkeypatch.setitem(sys.modules, "athena_runtime", types.SimpleNamespace(rt=lambda: _FakeRt()))

    base = _candles(3, vol=42.0)
    out, source = scalp_engine._overlay_eodhd_volume_for_scalp(
        "AAPL", "stock", "M15", list(base), live=True
    )

    assert calls
    assert source == "ws_tick"
    assert out[0]["vol"] == 9999.0
    assert out[0]["open"] == base[0]["open"]


def test_backtest_commodity_still_uses_eodhd(monkeypatch):
    calls = []

    class _FakeRt:
        def fetch_eodhd_volume_only(self, pair, tf, limit, *, cache_only=True, grid_offset_seconds=0):
            calls.append((pair, tf, limit, cache_only))
            return {
                "volume_source": "eodhd_1m",
                "candles": [
                    {
                        "time": "2026-03-31T08:00:00+00:00",
                        "open": 1,
                        "high": 1,
                        "low": 1,
                        "close": 1,
                        "vol": 5000.0,
                    },
                    {
                        "time": "2026-03-31T08:01:00+00:00",
                        "open": 1,
                        "high": 1,
                        "low": 1,
                        "close": 1,
                        "vol": 6000.0,
                    },
                    {
                        "time": "2026-03-31T08:02:00+00:00",
                        "open": 1,
                        "high": 1,
                        "low": 1,
                        "close": 1,
                        "vol": 7000.0,
                    },
                ],
            }

    monkeypatch.setitem(
        scalp_engine.CONFIG,
        "SCALP_ENGINE",
        {
            **scalp_engine.CONFIG.get("SCALP_ENGINE", {}),
            "EODHD_VOLUME_OVERLAY_LIVE_ENABLED": True,
            "EODHD_VOLUME_OVERLAY_BACKTEST_ENABLED": True,
            "EODHD_VOLUME_OVERLAY_LIVE_ASSET_TYPES": ["stock"],
        },
    )
    monkeypatch.setitem(sys.modules, "athena_runtime", types.SimpleNamespace(rt=lambda: _FakeRt()))

    base = _candles(3, vol=42.0)
    out, source = scalp_engine._overlay_eodhd_volume_for_scalp(
        "XAU/USD", "commodity", "M15", list(base), live=False
    )

    assert calls
    assert calls[0][3] is False
    assert source == "eodhd_1m"
    assert out[0]["vol"] == 5000.0


def test_scalp_mt5_fetch_prefers_real_volume(monkeypatch):
    fake_mt5 = types.SimpleNamespace(
        TIMEFRAME_M1=1,
        TIMEFRAME_M5=5,
        TIMEFRAME_M15=15,
        TIMEFRAME_H1=60,
        symbol_select=lambda s, e: True,
        copy_rates_from_pos=lambda s, tf, start, count: [
            _FakeRateRow(
                time=1,
                open=100,
                high=101,
                low=99,
                close=100.5,
                tick_volume=10,
                real_volume=250,
            ),
        ],
    )
    monkeypatch.setitem(sys.modules, "MetaTrader5", fake_mt5)
    monkeypatch.setitem(
        sys.modules,
        "mt5_executor",
        types.SimpleNamespace(mt5_connect=lambda: True),
    )

    candles = scalp_engine.mt5_fetch_scalp_candles("XAUUSD", "M15", 1, include_forming=True)
    assert len(candles) == 1
    assert candles[0]["vol"] == 250.0


def test_scalp_mt5_fetch_falls_back_to_tick_volume(monkeypatch):
    fake_mt5 = types.SimpleNamespace(
        TIMEFRAME_M1=1,
        TIMEFRAME_M5=5,
        TIMEFRAME_M15=15,
        TIMEFRAME_H1=60,
        symbol_select=lambda s, e: True,
        copy_rates_from_pos=lambda s, tf, start, count: [
            _FakeRateRow(
                time=1,
                open=100,
                high=101,
                low=99,
                close=100.5,
                tick_volume=10,
                real_volume=0,
            ),
            _FakeRateRow(
                time=2,
                open=101,
                high=102,
                low=100,
                close=101.5,
                tick_volume=12,
            ),
        ],
    )
    monkeypatch.setitem(sys.modules, "MetaTrader5", fake_mt5)
    monkeypatch.setitem(
        sys.modules,
        "mt5_executor",
        types.SimpleNamespace(mt5_connect=lambda: True),
    )

    candles = scalp_engine.mt5_fetch_scalp_candles("NAS100", "M15", 2, include_forming=True)
    assert len(candles) == 2
    assert candles[0]["vol"] == 10.0
    assert candles[1]["vol"] == 12.0
