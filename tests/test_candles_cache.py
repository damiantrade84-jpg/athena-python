from config import CONFIG
from candles_cache import fetch_candles, get_candle_fetch_meta, resample_from_h1


def _hourly_series(start_hour: int, count: int) -> list[dict]:
    rows = []
    for i in range(count):
        hour = start_hour + i
        rows.append(
            {
                "time": f"2026-03-25T{hour:02d}:00:00Z",
                "open": 1.0 + i,
                "high": 1.5 + i,
                "low": 0.5 + i,
                "close": 1.2 + i,
                "vol": 100 + i,
            }
        )
    return rows


def test_h4_resample_uses_utc_boundaries_by_default():
    candles = _hourly_series(16, 8)

    resampled = resample_from_h1(candles, "H4", 20)

    assert [c["time"][11:16] for c in resampled] == ["16:00", "20:00"]


def test_h4_resample_can_shift_forex_alignment_by_two_hours():
    candles = _hourly_series(16, 8)

    resampled = resample_from_h1(
        candles,
        "H4",
        20,
        alignment_offset_hours=2,
    )

    assert [c["time"][11:16] for c in resampled] == ["14:00", "18:00", "22:00"]


def _unused_fetcher(*_args, **_kwargs):
    raise AssertionError("unexpected fetcher call")


def _fallback_candles():
    return [
        {
            "time": "2026-03-25T12:00:00Z",
            "open": 1.0,
            "high": 1.2,
            "low": 0.9,
            "close": 1.1,
            "vol": 100.0,
        }
    ]


def test_mt5_error_payload_fallback_candles_are_blocked_by_default(monkeypatch):
    monkeypatch.setitem(CONFIG, "MT5_CANDLE_FALLBACK_ENABLED", False)
    pair = {"display": "EUR/USD", "symbol": "EURUSD", "type": "forex", "source": "mt5"}

    out = fetch_candles(
        pair,
        "H1",
        10,
        fetch_candles_live=_unused_fetcher,
        fetch_binance=_unused_fetcher,
        fetch_eodhd=_unused_fetcher,
        fetch_polygon=_unused_fetcher,
        fetch_yfinance=_unused_fetcher,
        fetch_mt5=lambda *_args, **_kwargs: {
            "error": True,
            "detail": "MT5 not connected",
            "candles": _fallback_candles(),
        },
        tf_b={},
    )

    assert out is None
    meta = get_candle_fetch_meta(pair, "H1", 10)
    assert meta["error"] is True
    assert meta["fallback"] == "blocked_mt5_error_candles"
    assert meta["fallback_used"] is False
    assert meta["primary_provider"] == "mt5"
    assert meta["bars"] == 0


def test_eodhd_index_does_not_fall_back_to_yfinance():
    pair = {"display": "ASX 200", "symbol": "^AXJO", "type": "index", "source": "eodhd"}
    yfinance_calls = []

    out = fetch_candles(
        pair,
        "H4",
        10,
        fetch_candles_live=_unused_fetcher,
        fetch_binance=_unused_fetcher,
        fetch_eodhd=lambda *_args, **_kwargs: None,
        fetch_polygon=_unused_fetcher,
        fetch_yfinance=lambda *_args, **_kwargs: yfinance_calls.append(True) or _fallback_candles(),
        yfinance_symbol_for_pair=lambda _pair: "^AXJO",
        fetch_mt5=_unused_fetcher,
        tf_b={},
    )

    assert out is None
    assert yfinance_calls == []


def test_mt5_error_payload_fallback_candles_can_be_explicitly_enabled(monkeypatch):
    monkeypatch.setitem(CONFIG, "MT5_CANDLE_FALLBACK_ENABLED", True)
    pair = {"display": "GBP/USD", "symbol": "GBPUSD", "type": "forex", "source": "mt5"}

    out = fetch_candles(
        pair,
        "H1",
        10,
        fetch_candles_live=_unused_fetcher,
        fetch_binance=_unused_fetcher,
        fetch_eodhd=_unused_fetcher,
        fetch_polygon=_unused_fetcher,
        fetch_yfinance=_unused_fetcher,
        fetch_mt5=lambda *_args, **_kwargs: {
            "error": True,
            "detail": "MT5 not connected",
            "candles": _fallback_candles(),
        },
        tf_b={},
    )

    assert out == _fallback_candles()
    meta = get_candle_fetch_meta(pair, "H1", 10)
    assert meta["error"] is True
    assert meta["bars"] == 1
