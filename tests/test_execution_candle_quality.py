"""Tests for execution-time candle freshness hydration and D1 calendar-gap policy."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock


def test_forex_mt5_d1_calendar_gap_downgrades_multi_bucket(monkeypatch) -> None:
    """Weekend D1 lag on MT5 should not be stale_multi_bucket within grace cap."""
    import athena_app.services.market_state as ms

    monkeypatch.setitem(ms.CONFIG, "MT5_D1_CALENDAR_GAP_GRACE_BUCKETS", 4)

    pair = {
        "display": "EUR/USD",
        "symbol": "EURUSD",
        "type": "forex",
        "source": "mt5",
    }
    ts_fri = int(datetime(2026, 5, 8, 0, 0, tzinfo=timezone.utc).timestamp())
    bar = {"time": ts_fri, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0}
    series = [bar] * 50
    now = datetime(2026, 5, 11, 12, 30, tzinfo=timezone.utc).timestamp()

    diag = ms.candle_freshness_diagnostic(pair, "D1", series, time_now=now)
    assert diag["stalenessSeverity"] == "d1_calendar_gap_policy_ok"
    assert diag["bucketLag"] == 3


def test_commodity_mt5_d1_calendar_gap_downgrades(monkeypatch) -> None:
    """Logs showed Copper/WTI with Fri→Mon D1 lag; grace applies to MT5 non-crypto."""
    import athena_app.services.market_state as ms

    monkeypatch.setitem(ms.CONFIG, "MT5_D1_CALENDAR_GAP_GRACE_BUCKETS", 4)

    pair = {"display": "Copper", "type": "commodity", "source": "mt5"}
    ts_fri = int(datetime(2026, 5, 8, 0, 0, tzinfo=timezone.utc).timestamp())
    bar = {"time": ts_fri, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0}
    series = [bar] * 40
    now = datetime(2026, 5, 11, 12, 0, tzinfo=timezone.utc).timestamp()

    diag = ms.candle_freshness_diagnostic(pair, "D1", series, time_now=now)
    assert diag["stalenessSeverity"] == "d1_calendar_gap_policy_ok"


def test_crypto_mt5_d1_calendar_gap_grace_not_applied(monkeypatch) -> None:
    import athena_app.services.market_state as ms

    monkeypatch.setitem(ms.CONFIG, "MT5_D1_CALENDAR_GAP_GRACE_BUCKETS", 4)
    monkeypatch.setitem(ms.CONFIG, "MT5_D1_CALENDAR_GAP_EXCLUDE_TYPES", ["crypto"])

    pair = {"display": "BTCUSD", "type": "crypto", "source": "mt5"}
    ts_fri = int(datetime(2026, 5, 8, 0, 0, tzinfo=timezone.utc).timestamp())
    bar = {"time": ts_fri, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0}
    series = [bar] * 40
    now = datetime(2026, 5, 11, 12, 0, tzinfo=timezone.utc).timestamp()

    diag = ms.candle_freshness_diagnostic(pair, "D1", series, time_now=now)
    assert diag["stalenessSeverity"] == "stale_multi_bucket"


def test_forex_mt5_d1_calendar_gap_disabled_when_lag_exceeds_cap(monkeypatch) -> None:
    import athena_app.services.market_state as ms

    monkeypatch.setitem(ms.CONFIG, "MT5_D1_CALENDAR_GAP_GRACE_BUCKETS", 2)

    pair = {"display": "EUR/USD", "type": "forex", "source": "mt5"}
    ts = int(datetime(2026, 5, 4, 0, 0, tzinfo=timezone.utc).timestamp())
    series = [{"time": ts, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0}] * 20
    now = datetime(2026, 5, 11, 12, 0, tzinfo=timezone.utc).timestamp()

    diag = ms.candle_freshness_diagnostic(pair, "D1", series, time_now=now)
    assert diag["stalenessSeverity"] == "stale_multi_bucket"


def test_hydrate_empty_fetch_preserves_embedded_freshness(monkeypatch) -> None:
    """Incomplete fetches must not overwrite a prior analyze_pair refresh."""
    import execution

    mock_r = MagicMock()
    mock_r.CONFIG = {
        "EXECUTION_HYDRATE_CANDLE_QUALITY": True,
        "QUICK_EXEC_PREFETCH_CANDLE_META": False,
        "CANDLE_FRESHNESS_ENABLED": True,
        "SIGNAL_EXECUTABLE_FALSE_WHEN_FRESHNESS_BLOCKS": True,
    }
    mock_r.ALL_PAIRS = [{"display": "EUR/USD", "source": "mt5", "type": "forex"}]
    mock_r.fetch_candles = lambda *_a, **_k: []
    mock_r.log = MagicMock()
    monkeypatch.setattr(execution, "scan_candle_limits", lambda: {"H1": 10, "H4": 10, "D1": 10})

    sig = {
        "pair": "EUR/USD",
        "candleFreshness": {"H4": {"stalenessSeverity": "from_analyze"}},
        "candleFetchMeta": {"H4": {"from_analyze_meta": True}},
    }
    execution._hydrate_execution_candle_quality(sig, _r=mock_r)
    assert sig["candleFreshness"]["H4"]["stalenessSeverity"] == "from_analyze"


def test_hydrate_nonempty_fetch_repairs_poison_staleness(monkeypatch) -> None:
    import execution

    frozen = datetime(2026, 5, 11, 14, 0, 0, tzinfo=timezone.utc)

    class _MockDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return frozen

    monkeypatch.setattr(execution, "datetime", _MockDateTime)

    def _series(seconds: int, n: int) -> list[dict]:
        base = int(frozen.timestamp()) - n * seconds
        return [
            {"time": base + i * seconds, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0}
            for i in range(n)
        ]

    buckets = {"H1": _series(3600, 60), "H4": _series(14400, 60), "D1": _series(86400, 120)}

    def _fake_fetch(_pair: dict, tf: str, lim: int):
        lst = buckets.get(tf, [])
        return lst[-lim:] if lim else lst

    mock_r = MagicMock()
    mock_r.CONFIG = {
        "EXECUTION_HYDRATE_CANDLE_QUALITY": True,
        "QUICK_EXEC_PREFETCH_CANDLE_META": False,
        "CANDLE_FRESHNESS_ENABLED": True,
        "SIGNAL_EXECUTABLE_FALSE_WHEN_FRESHNESS_BLOCKS": True,
        "DATA_FRESHNESS_GATES": {"BLOCK_EXECUTION_ON_STALE": False},
    }
    mock_r.ALL_PAIRS = [{"display": "EUR/USD", "source": "mt5", "type": "forex"}]
    mock_r.fetch_candles = _fake_fetch
    mock_r.log = MagicMock()
    monkeypatch.setattr(execution, "scan_candle_limits", lambda: {"H1": 100, "H4": 100, "D1": 110})

    sig = {
        "pair": "EUR/USD",
        "display": "EUR/USD",
        "candleFreshness": {
            "H1": {"stalenessSeverity": "stale_multi_bucket"},
            "H4": {"stalenessSeverity": "stale_multi_bucket"},
            "D1": {"stalenessSeverity": "stale_multi_bucket"},
        },
    }

    execution._hydrate_execution_candle_quality(sig, _r=mock_r)
    assert "dataFreshness" in sig and isinstance(sig["dataFreshness"], dict)
    for tf in ("H1", "H4", "D1"):
        assert tf in sig.get("candleFreshness", {}), sig.get("candleFreshness")


def test_hydrate_reannotates_cache_meta_at_execution_time(monkeypatch) -> None:
    """Fresh candle hydrate must align cache meta bucket with execution time_now."""
    import execution
    from athena_app.services.market_state import get_bucket_start_epoch

    frozen = datetime(2026, 6, 6, 14, 1, 19, tzinfo=timezone.utc)

    class _MockDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return frozen

    monkeypatch.setattr(execution, "datetime", _MockDateTime)

    def _series(seconds: int, n: int) -> list[dict]:
        base = int(frozen.timestamp()) - n * seconds
        return [
            {"time": base + i * seconds, "open": 25.0, "high": 25.5, "low": 24.8, "close": 25.2}
            for i in range(n)
        ]

    buckets = {"H1": _series(3600, 60), "H4": _series(14400, 60), "D1": _series(86400, 120)}

    def _fake_fetch(_pair: dict, tf: str, lim: int):
        lst = buckets.get(tf, [])
        return lst[-lim:] if lim else lst

    mock_r = MagicMock()
    mock_r.CONFIG = {
        "EXECUTION_HYDRATE_CANDLE_QUALITY": True,
        "QUICK_EXEC_PREFETCH_CANDLE_META": False,
        "CANDLE_FRESHNESS_ENABLED": True,
        "DATA_FRESHNESS_GATES": {"BLOCK_EXECUTION_ON_STALE": False},
    }
    mock_r.ALL_PAIRS = [
        {"display": "ETC/USDT", "symbol": "ETCUSDT", "source": "bybit", "type": "crypto"}
    ]
    mock_r.fetch_candles = _fake_fetch
    mock_r.log = MagicMock()
    monkeypatch.setattr(execution, "scan_candle_limits", lambda: {"H1": 100, "H4": 100, "D1": 110})

    t_now = frozen.timestamp()
    stale_h1_bucket = int(get_bucket_start_epoch("H1", t_now - 7200, 0.0))
    sig = {
        "pair": "ETC/USDT",
        "display": "ETC/USDT",
        "type": "crypto",
        "candleFetchMeta": {
            "H1": {
                "expectedCurrentBucketEpoch": stale_h1_bucket,
                "offsetHours": 0.0,
                "stalenessSeverity": "fresh",
            }
        },
    }

    execution._hydrate_execution_candle_quality(sig, _r=mock_r)
    h1_meta = sig.get("candleFetchMeta", {}).get("H1", {})
    assert h1_meta.get("expectedCurrentBucketEpoch") == int(
        get_bucket_start_epoch("H1", t_now, 0.0)
    )
    assert "candleConsistency" in sig


def test_execution_freshness_allows_d1_calendar_gap_policy_ok() -> None:
    """d1_calendar_gap_policy_ok must not block execution."""
    from athena_app.services.data_freshness import evaluate_execution_data_freshness

    sig = {
        "pair": "EUR/USD",
        "type": "forex",
        "candleFetchMeta": {
            "D1": {"stalenessSeverity": "d1_calendar_gap_policy_ok", "bucketLag": 3},
        },
    }
    result = evaluate_execution_data_freshness(sig, {})
    assert result["allowed"] is True
    assert result["blocked"] == []
    assert all(w.get("severity") != "d1_calendar_gap_policy_ok" for w in result["warnings"])

