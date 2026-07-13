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


def test_mt5_d1_stale_refetch_decision_retries_bounded_non_crypto(monkeypatch) -> None:
    import athena_app.services.market_state as ms

    monkeypatch.setitem(ms.CONFIG, "MT5_D1_FETCH_RETRY_ENABLED", True)
    monkeypatch.setitem(ms.CONFIG, "MT5_D1_FETCH_RETRY_MAX_LAG", 7)

    pair = {"display": "EUR/GBP", "type": "forex", "source": "mt5"}
    now = datetime(2026, 6, 25, 7, 39, tzinfo=timezone.utc).timestamp()
    stale_d1 = int(datetime(2026, 6, 19, 0, 0, tzinfo=timezone.utc).timestamp())

    should, lag = ms.should_refetch_mt5_d1_stale(pair, stale_d1, time_now=now)

    assert should is True
    assert lag == 6


def test_mt5_d1_stale_refetch_decision_remains_fail_closed(monkeypatch) -> None:
    import athena_app.services.market_state as ms

    monkeypatch.setitem(ms.CONFIG, "MT5_D1_FETCH_RETRY_ENABLED", True)
    monkeypatch.setitem(ms.CONFIG, "MT5_D1_FETCH_RETRY_MAX_LAG", 7)

    now = datetime(2026, 6, 25, 7, 39, tzinfo=timezone.utc).timestamp()
    stale_d1 = int(datetime(2026, 6, 15, 0, 0, tzinfo=timezone.utc).timestamp())
    crypto = {"display": "BTCUSD", "type": "crypto", "source": "mt5"}
    non_mt5 = {"display": "EUR/GBP", "type": "forex", "source": "eodhd"}

    assert ms.should_refetch_mt5_d1_stale(crypto, stale_d1, time_now=now) == (False, 0)
    assert ms.should_refetch_mt5_d1_stale(non_mt5, stale_d1, time_now=now) == (False, 0)

    forex = {"display": "EUR/GBP", "type": "forex", "source": "mt5"}
    assert ms.should_refetch_mt5_d1_stale(forex, stale_d1, time_now=now) == (False, 10)

    monkeypatch.setitem(ms.CONFIG, "MT5_D1_FETCH_RETRY_ENABLED", False)
    retryable_d1 = int(datetime(2026, 6, 19, 0, 0, tzinfo=timezone.utc).timestamp())
    assert ms.should_refetch_mt5_d1_stale(forex, retryable_d1, time_now=now) == (False, 0)


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
    mock_r._fetch_ab_crypto_signal_candles = None
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


def test_hydrate_recomputes_stored_atr_freshness_with_current_policy(monkeypatch) -> None:
    """A scan-time ATR verdict must not survive a fresh execution-time hydrate."""
    import execution

    now = datetime.now(timezone.utc)

    def _series(seconds: int, n: int) -> list[dict]:
        base = int(now.timestamp()) - n * seconds
        return [
            {"time": base + i * seconds, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0}
            for i in range(n)
        ]

    buckets = {"H1": _series(3600, 60), "H4": _series(14400, 60), "D1": _series(86400, 120)}

    def _fake_fetch(_pair: dict, tf: str, lim: int):
        return buckets[tf][-lim:]

    mock_r = MagicMock()
    mock_r.CONFIG = {
        "EXECUTION_HYDRATE_CANDLE_QUALITY": True,
        "QUICK_EXEC_PREFETCH_CANDLE_META": False,
        "CANDLE_FRESHNESS_ENABLED": True,
        "DATA_FRESHNESS_GATES": {"BLOCK_EXECUTION_ON_STALE": False},
        "ATR_FRESHNESS": {
            "ENABLED": True,
            "BLOCK_EXECUTION_ON_STALE_ATR": True,
            "MAX_AGE_SECONDS": {"H4": 28800},
        },
    }
    mock_r.ALL_PAIRS = [{"display": "EUR/USD", "source": "mt5", "type": "forex"}]
    mock_r.fetch_candles = _fake_fetch
    mock_r._fetch_ab_crypto_signal_candles = None
    mock_r.log = MagicMock()
    monkeypatch.setattr(execution, "scan_candle_limits", lambda: {"H1": 100, "H4": 100, "D1": 110})

    h4_last_open = (now.timestamp() - 22_394)
    sig = {
        "pair": "EUR/USD",
        "display": "EUR/USD",
        "atrDiagnostics": {
            "atr_value": 0.005,
            "atr_tf": "H4",
            "atr_source": "mt5",
            "atr_candle_last_ts": h4_last_open,
            "atr_age_seconds": 22_394,
            "atr_confirmed_only": True,
        },
        "atrFreshness": {"stale": True, "would_block": True, "reason": "old_scan_verdict"},
    }

    execution._hydrate_execution_candle_quality(sig, _r=mock_r)

    assert sig["atrFreshness"]["stale"] is False
    assert sig["atrFreshness"]["would_block"] is False
    assert sig["atrFreshness"]["reason"] == "fresh"


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
    mock_r._fetch_ab_crypto_signal_candles = None
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


def test_scanner_stock_h4_confirmed_only_lag_sets_consistency_before_freshness() -> None:
    """Scan-time Engine A freshness must not poison US stock AI/risk context.

    US stock H4 uses a 3h offset grid. At 18:30 UTC the current bucket is 15:00;
    a confirmed-only Engine A path ending at 11:00 is exactly one bucket behind
    and must be marked CONFIRMED_ONLY_OK before execution freshness is evaluated.
    """
    import scanner

    def _bar(iso: str) -> dict:
        return {
            "time": int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()),
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.5,
        }

    now = datetime(2026, 5, 11, 18, 30, tzinfo=timezone.utc).timestamp()
    pair = {"display": "AAPL", "symbol": "AAPL", "type": "stock", "source": "mt5"}
    signal = {"pair": "AAPL", "type": "stock"}
    market_state = {
        "D1": {"confirmed": [_bar("2026-05-11T00:00:00Z")], "forming": None},
        "H4": {
            "confirmed": [_bar("2026-05-11T07:00:00Z"), _bar("2026-05-11T11:00:00Z")],
            "forming": None,
        },
        "H1": {"confirmed": [_bar("2026-05-11T18:00:00Z")], "forming": None},
    }
    raw_candles = {tf: list(state["confirmed"]) for tf, state in market_state.items()}

    scanner._attach_engine_a_execution_freshness(
        signal,
        pair,
        preloaded_market_state=market_state,
        raw_candles=raw_candles,
        config={
            "DATA_FRESHNESS_GATES": {
                "BLOCK_EXECUTION_ON_STALE": True,
                "BLOCK_TIMEFRAMES": ["H4"],
                "BLOCK_SEVERITIES": ["stale_1_bucket"],
            },
            "SIGNAL_EXECUTABLE_FALSE_WHEN_FRESHNESS_BLOCKS": True,
        },
        time_now=now,
    )

    assert signal["candleFreshness"]["H4"]["stalenessSeverity"] == "stale_1_bucket"
    assert signal["candleConsistency"]["H4"]["status"] == "CONFIRMED_ONLY_OK"
    assert signal["dataFreshness"]["allowed"] is True
    assert signal["dataFreshness"]["blocked"] == []
