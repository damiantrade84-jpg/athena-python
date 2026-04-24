import candles_cache
from candles_cache import _annotate_fetch_meta_with_bar_freshness, fetch_candles, get_candle_fetch_meta
from config import CONFIG


def _epoch(iso_ts: str) -> int:
    from datetime import datetime

    return int(datetime.fromisoformat(iso_ts.replace("Z", "+00:00")).timestamp())


def test_annotate_fetch_meta_with_bar_freshness_marks_recent_bar():
    meta = {}
    candles = [{"time": 1_000, "close": 1.0}]

    out = _annotate_fetch_meta_with_bar_freshness(
        meta,
        candles,
        "H1",
        now=2_200.0,
    )

    assert out["lastBarTime"] == 1_000
    assert out["lastBarAgeSec"] == 1200.0
    assert out["lastBarStale"] is False


def test_annotate_fetch_meta_with_bar_freshness_marks_stale_bar():
    meta = {}
    candles = [{"time": 0, "close": 1.0}]

    out = _annotate_fetch_meta_with_bar_freshness(
        meta,
        candles,
        "H1",
        now=37_200.0,
    )

    assert out["lastBarAgeSec"] == 37200.0
    assert out["lastBarStale"] is True


def test_annotate_fetch_meta_detects_h4_one_bucket_lag_for_live_feed():
    meta = {}
    candles = [{"time": _epoch("2026-04-24T05:00:00Z"), "close": 1.0}]

    out = _annotate_fetch_meta_with_bar_freshness(
        meta,
        candles,
        "H4",
        now=_epoch("2026-04-24T09:30:00Z"),
        offset_hours=1,
        live_feed=True,
    )

    assert out["lastBarEpoch"] == _epoch("2026-04-24T05:00:00Z")
    assert out["expectedCurrentBucketEpoch"] == _epoch("2026-04-24T09:00:00Z")
    assert out["bucketLag"] == 1
    assert out["hasCurrentBucket"] is False
    assert out["stalenessSeverity"] == "stale_1_bucket"
    assert out["lastBarStale"] is True


def test_live_forex_fetch_meta_exposes_h4_one_bucket_lag(monkeypatch):
    monkeypatch.setitem(CONFIG, "FOREX_H4_RESAMPLE_OFFSET_HOURS", 1.0)
    monkeypatch.setattr(
        candles_cache.time,
        "time",
        lambda: float(_epoch("2026-04-24T09:30:00Z")),
    )
    pair = {
        "display": "EUR/USD",
        "symbol": "EURUSD_META_TEST",
        "type": "forex",
        "source": "mt5",
    }
    candles = [{"time": _epoch("2026-04-24T05:00:00Z"), "close": 1.0}]

    out = fetch_candles(
        pair,
        "H4",
        10,
        fetch_candles_live=lambda *_args, **_kwargs: None,
        fetch_binance=lambda *_args, **_kwargs: None,
        fetch_binance_paginated=None,
        fetch_eodhd=lambda *_args, **_kwargs: None,
        fetch_polygon=lambda *_args, **_kwargs: None,
        fetch_yfinance=lambda *_args, **_kwargs: None,
        fetch_mt5=lambda *_args, **_kwargs: list(candles),
        yfinance_symbol_for_pair=lambda _pair: None,
        tf_b={"H4": "4h"},
    )
    meta = get_candle_fetch_meta(pair, "H4", 10)

    assert out == candles
    assert meta["cacheBypass"] is True
    assert meta["upstream"] == "mt5"
    assert meta["expectedCurrentBucketEpoch"] == _epoch("2026-04-24T09:00:00Z")
    assert meta["bucketLag"] == 1
    assert meta["hasCurrentBucket"] is False
    assert meta["stalenessSeverity"] == "stale_1_bucket"
