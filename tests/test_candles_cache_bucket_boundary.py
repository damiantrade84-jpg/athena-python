"""Bucket-boundary TTL cache invalidation tests (EODHD/JSE path)."""

from datetime import datetime, timezone

from candles_cache import _CANDLE_CACHE_TTL, _cache_key, _ttl_cache_last_bar_stale


def _epoch(iso: str) -> float:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def test_h1_cache_not_stale_within_one_bucket_lag():
    """H1 cache with last bar one bucket behind should still be servable."""
    pair = {"type": "stock", "source": "eodhd", "display": "NPN.JO"}
    candles = [{"time": "2026-05-20T10:00:00Z", "close": 1.0}]
    now = _epoch("2026-05-20T11:30:00Z")
    assert _ttl_cache_last_bar_stale(candles, "H1", now, pair=pair) is False


def test_h1_cache_stale_after_two_bucket_lag():
    """H1 cache must invalidate when lag >= 2 buckets."""
    pair = {"type": "stock", "source": "eodhd", "display": "NPN.JO"}
    candles = [{"time": "2026-05-20T08:00:00Z", "close": 1.0}]
    now = _epoch("2026-05-20T11:30:00Z")
    assert _ttl_cache_last_bar_stale(candles, "H1", now, pair=pair) is True


def test_h4_cache_stale_after_two_bucket_lag():
    pair = {"type": "stock", "source": "eodhd", "display": "AAPL.US"}
    candles = [{"time": "2026-05-20T03:00:00Z", "close": 1.0}]
    now = _epoch("2026-05-20T15:00:00Z")
    assert _ttl_cache_last_bar_stale(candles, "H4", now, pair=pair) is True


def test_d1_cache_one_bucket_lag_allowed():
    pair = {"type": "stock", "source": "eodhd", "display": "SOL.JO"}
    candles = [{"time": "2026-05-19T00:00:00Z", "close": 1.0}]
    now = _epoch("2026-05-20T12:00:00Z")
    assert _ttl_cache_last_bar_stale(candles, "D1", now, pair=pair) is False


def test_cache_key_includes_limit_not_provider():
    """Cache key is (symbol, tf, limit) — provider/confirmed are post-fetch."""
    pair_a = {"symbol": "TEST.US", "display": "TEST", "source": "eodhd", "type": "stock"}
    pair_b = {"symbol": "TEST.US", "display": "TEST", "source": "mt5", "type": "stock"}
    assert _cache_key(pair_a, "H1", 300) == _cache_key(pair_b, "H1", 300)
    assert _cache_key(pair_a, "H1", 300) != _cache_key(pair_a, "H1", 500)


def test_h1_ttl_is_55_minutes():
    assert _CANDLE_CACHE_TTL["H1"] == 55 * 60
