from datetime import datetime

from athena_app.services.market_state import candle_freshness_diagnostic
from config import CONFIG


def _epoch(iso_text):
    return int(datetime.fromisoformat(iso_text.replace("Z", "+00:00")).timestamp())


def _candle(iso_text):
    return {
        "time": iso_text,
        "open": 1.0,
        "high": 1.1,
        "low": 0.9,
        "close": 1.05,
    }


def test_forex_h4_freshness_uses_offset_and_marks_active_bucket_forming(monkeypatch):
    monkeypatch.setitem(CONFIG, "FOREX_H4_RESAMPLE_OFFSET_HOURS", 1)
    pair = {"type": "forex", "display": "EUR/USD", "symbol": "EURUSD", "source": "mt5"}

    diagnostic = candle_freshness_diagnostic(
        pair,
        "H4",
        [_candle("2026-04-24T01:00:00Z"), _candle("2026-04-24T05:00:00Z")],
        time_now=_epoch("2026-04-24T06:30:00Z"),
    )

    assert diagnostic["symbol"] == "EURUSD"
    assert diagnostic["source"] == "mt5"
    assert diagnostic["timeframe"] == "H4"
    assert diagnostic["expectedCurrentBucketEpoch"] == _epoch("2026-04-24T05:00:00Z")
    assert diagnostic["expectedCurrentBucketIso"] == "2026-04-24T05:00:00Z"
    assert diagnostic["lastBarEpoch"] == _epoch("2026-04-24T05:00:00Z")
    assert diagnostic["lastBarIso"] == "2026-04-24T05:00:00Z"
    assert diagnostic["bucketLag"] == 0
    assert diagnostic["hasCurrentBucket"] is True
    assert diagnostic["stalenessSeverity"] == "fresh"
    assert diagnostic["confirmedCount"] == 1
    assert diagnostic["formingCount"] == 1
    assert diagnostic["usesOffset"] is True
    assert diagnostic["offsetHours"] == 1.0


def test_forex_h4_previous_offset_bucket_is_confirmed_and_one_bucket_lag(monkeypatch):
    monkeypatch.setitem(CONFIG, "FOREX_H4_RESAMPLE_OFFSET_HOURS", 1)
    pair = {"type": "forex", "display": "EUR/USD", "symbol": "EURUSD", "source": "mt5"}

    diagnostic = candle_freshness_diagnostic(
        pair,
        "H4",
        [_candle("2026-04-24T01:00:00Z")],
        time_now=_epoch("2026-04-24T06:30:00Z"),
    )

    assert diagnostic["expectedCurrentBucketEpoch"] == _epoch("2026-04-24T05:00:00Z")
    assert diagnostic["lastBarEpoch"] == _epoch("2026-04-24T01:00:00Z")
    assert diagnostic["bucketLag"] == 1
    assert diagnostic["hasCurrentBucket"] is False
    assert diagnostic["stalenessSeverity"] == "stale_1_bucket"
    assert diagnostic["confirmedCount"] == 1
    assert diagnostic["formingCount"] == 0
    assert diagnostic["usesOffset"] is True


def test_crypto_h4_freshness_ignores_forex_offset(monkeypatch):
    monkeypatch.setitem(CONFIG, "FOREX_H4_RESAMPLE_OFFSET_HOURS", 1)
    pair = {
        "type": "crypto",
        "display": "BTC/USDT",
        "symbol": "BTCUSDT",
        "source": "binance",
    }

    diagnostic = candle_freshness_diagnostic(
        pair,
        "H4",
        [_candle("2026-04-24T00:00:00Z"), _candle("2026-04-24T04:00:00Z")],
        time_now=_epoch("2026-04-24T06:30:00Z"),
    )

    assert diagnostic["expectedCurrentBucketEpoch"] == _epoch("2026-04-24T04:00:00Z")
    assert diagnostic["hasCurrentBucket"] is True
    assert diagnostic["stalenessSeverity"] == "fresh"
    assert diagnostic["confirmedCount"] == 1
    assert diagnostic["formingCount"] == 1
    assert diagnostic["usesOffset"] is False
    assert diagnostic["offsetHours"] == 0.0


def test_crypto_d1_freshness_ignores_forex_offset(monkeypatch):
    monkeypatch.setitem(CONFIG, "FOREX_H4_RESAMPLE_OFFSET_HOURS", 1)
    pair = {
        "type": "crypto",
        "display": "BTC/USDT",
        "symbol": "BTCUSDT",
        "source": "binance",
    }

    diagnostic = candle_freshness_diagnostic(
        pair,
        "D1",
        [_candle("2026-04-23T00:00:00Z"), _candle("2026-04-24T00:00:00Z")],
        time_now=_epoch("2026-04-24T12:00:00Z"),
    )

    assert diagnostic["expectedCurrentBucketEpoch"] == _epoch("2026-04-24T00:00:00Z")
    assert diagnostic["hasCurrentBucket"] is True
    assert diagnostic["stalenessSeverity"] == "fresh"
    assert diagnostic["confirmedCount"] == 1
    assert diagnostic["formingCount"] == 1
    assert diagnostic["usesOffset"] is False
    assert diagnostic["offsetHours"] == 0.0
