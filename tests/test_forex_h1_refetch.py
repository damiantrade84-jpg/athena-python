"""Unit tests for the forex H1 one-shot refetch decision (should_refetch_forex_stale_tf).

Covers the STALE_DATA_PRE_SCORING:H1 false-positive guard: MT5 can serve stale H1
history (boundary omission or lazy chart re-sync right after a terminal/server
restart), so the newest fetched bar lags the current bucket. A retry is warranted only
for a small lag window (2..MAX_LAG); normal confirmed-only lag (1), fresh (0), big
weekend/dead-feed gaps, non-forex, unsupported TFs, and disabled config must NOT retry.

Times are canonical UTC-aligned bar times (caller shifts broker-stamped labels first;
see should_refetch_forex_stale_tf docstring — H1 is not shift-invariant).
"""

import pytest

from config import CONFIG
from athena_app.services.market_state import (
    get_bucket_start_epoch,
    should_refetch_forex_h4,
    should_refetch_forex_stale_tf,
)

_NOW = 1781034017  # 2026-06-09T19:40:17Z (from the real EUR/AUD stale-event logs)
_H1 = 3600
_FOREX = {"type": "forex", "source": "mt5", "display": "EUR/AUD", "symbol": "EURAUD"}


@pytest.fixture(autouse=True)
def _forex_h1_retry_cfg(monkeypatch):
    monkeypatch.setitem(CONFIG, "MT5_H1_FETCH_RETRY_ENABLED", True)
    monkeypatch.setitem(CONFIG, "MT5_H1_FETCH_RETRY_MAX_LAG", 6)


def _bar_for_lag(lag: int) -> int:
    now_bucket = get_bucket_start_epoch("H1", _NOW, offset_hours=0.0)
    return int(now_bucket) - lag * _H1


@pytest.mark.parametrize(
    "lag,expected_retry",
    [
        (0, False),  # forming bar present -> fresh
        (1, False),  # normal confirmed-only lag -> exempt, no retry
        (2, True),   # missing the just-closed bar -> retry
        (4, True),   # post-restart lazy re-sync (observed 2026-06-09) -> retry
        (6, True),   # at the cap -> retry
        (7, False),  # beyond cap (dead feed) -> no retry
        (48, False), # weekend hole -> no retry
    ],
)
def test_lag_window(lag, expected_retry):
    should, observed_lag = should_refetch_forex_stale_tf(
        _FOREX, "H1", _bar_for_lag(lag), time_now=_NOW
    )
    assert observed_lag == lag
    assert should is expected_retry


def test_non_forex_never_retries():
    stock = {"type": "stock", "source": "mt5", "display": "AAPL", "symbol": "AAPL"}
    should, lag = should_refetch_forex_stale_tf(stock, "H1", _bar_for_lag(4), time_now=_NOW)
    assert should is False
    assert lag == 0


def test_unsupported_tf_never_retries():
    for tf in ("M15", "D1", ""):
        assert should_refetch_forex_stale_tf(_FOREX, tf, _bar_for_lag(4), time_now=_NOW) == (
            False,
            0,
        )


def test_disabled_config_never_retries(monkeypatch):
    monkeypatch.setitem(CONFIG, "MT5_H1_FETCH_RETRY_ENABLED", False)
    should, _ = should_refetch_forex_stale_tf(_FOREX, "H1", _bar_for_lag(4), time_now=_NOW)
    assert should is False


def test_h1_disable_does_not_affect_h4(monkeypatch):
    monkeypatch.setitem(CONFIG, "MT5_H1_FETCH_RETRY_ENABLED", False)
    monkeypatch.setitem(CONFIG, "MT5_H4_FETCH_RETRY_ENABLED", True)
    monkeypatch.setitem(CONFIG, "FOREX_H4_RESAMPLE_OFFSET_HOURS", 1.0)
    h4_bar = int(get_bucket_start_epoch("H4", _NOW, offset_hours=1.0)) - 2 * 14400
    assert should_refetch_forex_stale_tf(_FOREX, "H4", h4_bar, time_now=_NOW) == (True, 2)


def test_custom_max_lag(monkeypatch):
    monkeypatch.setitem(CONFIG, "MT5_H1_FETCH_RETRY_MAX_LAG", 3)
    assert should_refetch_forex_stale_tf(_FOREX, "H1", _bar_for_lag(3), time_now=_NOW) == (True, 3)
    assert should_refetch_forex_stale_tf(_FOREX, "H1", _bar_for_lag(4), time_now=_NOW) == (False, 4)


def test_invalid_inputs():
    assert should_refetch_forex_stale_tf(None, "H1", _bar_for_lag(4), time_now=_NOW) == (False, 0)
    assert should_refetch_forex_stale_tf(_FOREX, "H1", None, time_now=_NOW) == (False, 0)
    assert should_refetch_forex_stale_tf(_FOREX, "H1", 0, time_now=_NOW) == (False, 0)


def test_h4_wrapper_delegates(monkeypatch):
    """should_refetch_forex_h4 keeps its original contract via the generalized helper."""
    monkeypatch.setitem(CONFIG, "MT5_H4_FETCH_RETRY_ENABLED", True)
    monkeypatch.setitem(CONFIG, "MT5_H4_FETCH_RETRY_MAX_LAG", 3)
    monkeypatch.setitem(CONFIG, "FOREX_H4_RESAMPLE_OFFSET_HOURS", 1.0)
    h4_bar = int(get_bucket_start_epoch("H4", _NOW, offset_hours=1.0)) - 2 * 14400
    assert should_refetch_forex_h4(_FOREX, h4_bar, time_now=_NOW) == (True, 2)
