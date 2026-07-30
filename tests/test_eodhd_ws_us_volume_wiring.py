"""EODHD US WebSocket volume wiring for MT5-routed US stocks.

The `us` stream is the only feed carrying real per-trade exchange volume, and
every stock pair is MT5-routed for OHLC. These tests pin the subscription
selection, the tick->bar timeframe set, and the overlay provenance that Engine B
keys volume eligibility off.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import candle_feeds
from candle_feeds import (
    _EODHD_WS_US_MAX_SYMBOLS,
    CandleBuilder,
    EODHDWebSocketManager,
    _eodhd_us_ws_ticker,
    eodhd_ws_us_stock_pairs,
)
from eodhd_volume_overlay import overlay_candle_volumes
from factor_scoring import (
    _h4_volume_vs_ma,
    _volume_provenance_uniform,
)


@pytest.fixture(autouse=True)
def _ignore_ambient_ws_symbol_config(monkeypatch):
    """Selection consults EODHD_WS_US_SYMBOLS in the real config.

    These fixtures use synthetic tickers, so pin the list off and let the tests
    that cover it opt back in explicitly.
    """
    monkeypatch.setattr(candle_feeds, "_configured_us_ws_displays", lambda: None)


def _core_stock(symbol, display, ws=True):
    return {
        "symbol": symbol,
        "type": "stock",
        "display": display,
        "source": "mt5",
        "enabled": True,
        "ws": ws,
    }


def _atfx_stock(ticker):
    """ATFX share CFDs carry a bare broker symbol plus an eodhdSymbol override."""
    return {
        "symbol": ticker,
        "type": "stock",
        "display": ticker,
        "source": "mt5",
        "enabled": True,
        "eodhdSymbol": f"{ticker}.US",
    }


def test_us_ws_ticker_resolves_core_and_atfx_symbols():
    assert _eodhd_us_ws_ticker(_core_stock("AAPL.US", "AAPL")) == "AAPL"
    # Bare ATFX ticker resolves through the vendor override, not the raw symbol.
    assert _eodhd_us_ws_ticker(_atfx_stock("ABNB")) == "ABNB"
    # Non-US listings and non-stock classes are not US-stream candidates.
    assert _eodhd_us_ws_ticker({"symbol": "SAP", "type": "stock", "eodhdSymbol": "SAP.XETRA"}) is None
    assert _eodhd_us_ws_ticker({"symbol": "700", "type": "stock", "eodhdSymbol": "0700.HK"}) is None
    assert _eodhd_us_ws_ticker({"symbol": "EURUSD", "type": "forex", "display": "EUR/USD"}) is None


def test_mt5_routed_us_stocks_are_subscribed_for_volume():
    """Subscription must not require source=eodhd — no pair has that any more."""
    mgr = EODHDWebSocketManager("k")
    info = mgr._build_ticker_map(
        [
            _core_stock("AAPL.US", "AAPL"),
            _atfx_stock("ABNB"),
            # MT5 forex/index carry no usable WS volume and stay off the stream.
            {
                "symbol": "EURUSD",
                "type": "forex",
                "display": "EUR/USD",
                "source": "mt5",
                "enabled": True,
            },
            {
                "symbol": "^GSPC",
                "type": "index",
                "display": "S&P 500",
                "source": "mt5",
                "enabled": True,
            },
        ]
    )
    assert info["us"] == ["AAPL", "ABNB"]
    assert info["map"]["AAPL"] == "AAPL"
    assert info["map"]["ABNB"] == "ABNB"
    assert info["forex"] == []


def test_live_v2_owned_pairs_are_not_double_counted():
    """ws:False marks LiveV2 membership; subscribing them too would double volume."""
    mgr = EODHDWebSocketManager("k")
    info = mgr._build_ticker_map(
        [
            _core_stock("AAPL.US", "AAPL", ws=True),
            _core_stock("AMZN.US", "AMZN", ws=False),
        ]
    )
    assert info["us"] == ["AAPL"]
    assert "AMZN" not in info["map"]


def test_us_subscription_is_capped_at_the_plan_limit():
    mgr = EODHDWebSocketManager("k")
    pairs = [_atfx_stock(f"T{i:03d}") for i in range(_EODHD_WS_US_MAX_SYMBOLS + 25)]
    info = mgr._build_ticker_map(pairs)
    assert len(info["us"]) == _EODHD_WS_US_MAX_SYMBOLS == 50
    assert len(info["map"]) == _EODHD_WS_US_MAX_SYMBOLS


def test_us_stock_tick_timeframes_cover_h1_h4_and_exclude_d1():
    """H1/H4 are what Engine A and Engine B score; D1 stays owned by bulk_update_d1."""
    tfs = CandleBuilder._TFS_TICK_US_STOCK
    assert tfs["H1"] == 3600
    assert tfs["H4"] == 14400
    assert {"M1", "M5", "M15"} <= set(tfs)
    assert "D1" not in tfs


def test_atfx_bare_tickers_use_the_us_stock_tick_timeframes():
    """Bare ATFX symbols must resolve as US stocks or they get D1 buckets and no M15."""
    cb = CandleBuilder.__new__(CandleBuilder)
    cb._us_stock_displays = set()
    cb._us_stock_displays_loaded = False

    pairs = [_core_stock("AAPL.US", "AAPL"), _atfx_stock("ABNB")]
    import athena_runtime

    class _Rt:
        ALL_PAIRS = pairs

    original = athena_runtime.rt
    athena_runtime.rt = lambda: _Rt()
    try:
        displays = cb._get_us_stock_displays()
    finally:
        athena_runtime.rt = original

    assert displays == {"AAPL", "ABNB"}


def test_overlay_stamps_provenance_only_on_replaced_bars():
    base = [
        {"time": "2026-07-29T13:00:00+00:00", "vol": 11.0},
        {"time": "2026-07-29T14:00:00+00:00", "vol": 12.0},
    ]
    volume = [{"time": "2026-07-29T14:00:00+00:00", "vol": 900.0}]

    merged, matched = overlay_candle_volumes(base, volume, "H1")

    assert matched == 1
    # Unmatched bar keeps MT5 tick volume and carries no provenance, so Engine B
    # cannot mistake it for traded volume.
    assert merged[0]["vol"] == 11.0
    assert "volSource" not in merged[0]
    assert merged[1]["vol"] == 900.0
    assert merged[1]["volSource"] == "eodhd"


def _bars(count, vol, vol_source=None):
    out = []
    for i in range(count):
        bar = {"time": f"2026-07-29T{i:02d}:00:00+00:00", "close": 100.0 + i, "vol": vol}
        if vol_source:
            bar["volSource"] = vol_source
        out.append(bar)
    return out


def test_provenance_uniform_accepts_single_source_windows():
    # All tick volume: the pre-overlay baseline, still allowed.
    assert _volume_provenance_uniform(_bars(20, 30_000.0), 20) is True
    # All EODHD volume: what the WS/seed backfill produces.
    assert _volume_provenance_uniform(_bars(20, 40_000_000.0, "eodhd"), 20) is True


def test_provenance_uniform_rejects_mixed_scale_window():
    """Tick counts and share counts differ ~1000x; a mixed mean is meaningless."""
    window = _bars(15, 30_000.0) + _bars(5, 40_000_000.0, "eodhd")
    assert _volume_provenance_uniform(window, 20) is False
    # Narrowing the lookback to the real-volume tail is uniform again.
    assert _volume_provenance_uniform(window, 5) is True


def test_h4_volume_vs_ma_refuses_mixed_provenance():
    mixed = _bars(15, 30_000.0) + _bars(5, 40_000_000.0, "eodhd")

    # Without the guard the ratio would be ~40M / ~10M = extreme "high volume".
    assert _h4_volume_vs_ma(mixed, 20) == (None, None)

    # A fully overlaid window still scores normally.
    latest, vol_ma = _h4_volume_vs_ma(_bars(20, 40_000_000.0, "eodhd"), 20)
    assert latest == 40_000_000.0
    assert vol_ma == 40_000_000.0


def test_seed_selection_matches_the_ws_subscription():
    pairs = [
        _core_stock("AAPL.US", "AAPL"),
        _core_stock("AMZN.US", "AMZN", ws=False),  # LiveV2-owned
        _atfx_stock("ABNB"),
        {"symbol": "EURUSD", "type": "forex", "display": "EUR/USD", "source": "mt5"},
    ]
    seeded = [p["display"] for p in eodhd_ws_us_stock_pairs(pairs)]
    subscribed = list(EODHDWebSocketManager("k")._build_ticker_map(pairs)["map"].values())
    assert seeded == subscribed == ["AAPL", "ABNB"]


def test_seed_selection_honours_the_same_cap():
    pairs = [_atfx_stock(f"T{i:03d}") for i in range(_EODHD_WS_US_MAX_SYMBOLS + 10)]
    assert len(eodhd_ws_us_stock_pairs(pairs)) == _EODHD_WS_US_MAX_SYMBOLS


def test_configured_symbols_choose_the_subscription_and_its_order(monkeypatch):
    """Without the config list the cap takes pair order — alphabetical accident."""
    pairs = [_atfx_stock(t) for t in ("AMC", "NVDA", "ORCL", "ATHM")]
    monkeypatch.setattr(
        candle_feeds, "_configured_us_ws_displays", lambda: ["ORCL", "NVDA"]
    )
    assert [p["display"] for p in eodhd_ws_us_stock_pairs(pairs)] == ["ORCL", "NVDA"]


def test_configured_symbols_skip_unknown_and_ineligible_names(monkeypatch):
    """A stale or non-US entry must not silently consume one of the 50 slots."""
    pairs = [
        _atfx_stock("ORCL"),
        _core_stock("AMZN.US", "AMZN", ws=False),  # LiveV2-owned
        {"symbol": "SAP", "type": "stock", "display": "SAP", "eodhdSymbol": "SAP.XETRA"},
    ]
    monkeypatch.setattr(
        candle_feeds,
        "_configured_us_ws_displays",
        lambda: ["DELISTED", "AMZN", "SAP", "ORCL"],
    )
    assert [p["display"] for p in eodhd_ws_us_stock_pairs(pairs)] == ["ORCL"]


def test_configured_symbols_still_respect_the_cap(monkeypatch):
    tickers = [f"T{i:03d}" for i in range(_EODHD_WS_US_MAX_SYMBOLS + 10)]
    pairs = [_atfx_stock(t) for t in tickers]
    monkeypatch.setattr(candle_feeds, "_configured_us_ws_displays", lambda: tickers)
    assert len(eodhd_ws_us_stock_pairs(pairs)) == _EODHD_WS_US_MAX_SYMBOLS


def test_empty_config_list_falls_back_to_pair_order(monkeypatch):
    pairs = [_atfx_stock("AMC"), _atfx_stock("ORCL")]
    monkeypatch.setattr(candle_feeds, "_configured_us_ws_displays", lambda: None)
    assert [p["display"] for p in eodhd_ws_us_stock_pairs(pairs)] == ["AMC", "ORCL"]


def test_configured_selection_drives_the_actual_subscription(monkeypatch):
    """The ticker map must follow the config, not its own scan of the pair list."""
    pairs = [_atfx_stock(t) for t in ("AMC", "ORCL", "NVDA")]
    monkeypatch.setattr(candle_feeds, "_configured_us_ws_displays", lambda: ["NVDA", "ORCL"])
    info = EODHDWebSocketManager("k")._build_ticker_map(pairs)
    assert info["us"] == ["NVDA", "ORCL"]
    assert [p["display"] for p in eodhd_ws_us_stock_pairs(pairs)] == list(info["map"].values())
