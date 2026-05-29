"""Unit tests for the forex H4 one-shot refetch decision (should_refetch_forex_h4).

Covers the STALE_DATA_PRE_SCORING:H4 false-positive guard: MT5 can briefly omit the
just-closed/forming H4 bar, so the newest fetched bar lags the current bucket. A retry
is warranted only for a small lag window (2..MAX_LAG); normal confirmed-only lag (1),
fresh (0), big weekend/dead-feed gaps, non-forex, and disabled config must NOT retry.
"""

import pytest

from config import CONFIG
from athena_app.services.market_state import (
    get_bucket_start_epoch,
    should_refetch_forex_h4,
)

_NOW = 1779385153  # 2026-05-21T17:39:13Z (from the real stale-event logs)
_H4 = 14400
_FOREX = {"type": "forex", "source": "mt5", "display": "GBP/USD", "symbol": "GBPUSD"}


@pytest.fixture(autouse=True)
def _forex_h4_offset(monkeypatch):
    # GMT+3 broker -> offset-1 H4 grid; pin for determinism.
    monkeypatch.setitem(CONFIG, "FOREX_H4_RESAMPLE_OFFSET_HOURS", 1.0)
    monkeypatch.setitem(CONFIG, "MT5_H4_FETCH_RETRY_ENABLED", True)
    monkeypatch.setitem(CONFIG, "MT5_H4_FETCH_RETRY_MAX_LAG", 3)


def _bar_for_lag(lag: int) -> int:
    now_bucket = get_bucket_start_epoch("H4", _NOW, offset_hours=1.0)
    return int(now_bucket) - lag * _H4


@pytest.mark.parametrize(
    "lag,expected_retry",
    [
        (0, False),  # forming bar present -> fresh
        (1, False),  # normal confirmed-only lag -> exempt, no retry
        (2, True),   # missing the just-closed bar -> retry
        (3, True),   # at the cap -> retry
        (4, False),  # beyond cap (e.g. weekend hole) -> no retry
    ],
)
def test_lag_window(lag, expected_retry):
    should, observed_lag = should_refetch_forex_h4(
        _FOREX, _bar_for_lag(lag), time_now=_NOW
    )
    assert observed_lag == lag
    assert should is expected_retry


def test_shift_invariant_raw_broker_stamp():
    """Raw broker-stamped (+3h) newest bar yields the same lag as the canonical stamp."""
    raw = _bar_for_lag(2) + 3 * 3600  # GMT+3 broker label = canonical + 3h
    should, lag = should_refetch_forex_h4(_FOREX, raw, time_now=_NOW)
    assert (should, lag) == (True, 2)


def test_non_forex_never_retries():
    stock = {"type": "stock", "source": "mt5", "display": "AAPL", "symbol": "AAPL"}
    should, lag = should_refetch_forex_h4(stock, _bar_for_lag(2), time_now=_NOW)
    assert should is False
    assert lag == 0


def test_disabled_config_never_retries(monkeypatch):
    monkeypatch.setitem(CONFIG, "MT5_H4_FETCH_RETRY_ENABLED", False)
    should, _ = should_refetch_forex_h4(_FOREX, _bar_for_lag(2), time_now=_NOW)
    assert should is False


def test_custom_max_lag(monkeypatch):
    monkeypatch.setitem(CONFIG, "MT5_H4_FETCH_RETRY_MAX_LAG", 5)
    assert should_refetch_forex_h4(_FOREX, _bar_for_lag(5), time_now=_NOW) == (True, 5)
    assert should_refetch_forex_h4(_FOREX, _bar_for_lag(6), time_now=_NOW) == (False, 6)


def test_invalid_inputs():
    assert should_refetch_forex_h4(None, _bar_for_lag(2), time_now=_NOW) == (False, 0)
    assert should_refetch_forex_h4(_FOREX, None, time_now=_NOW) == (False, 0)
    assert should_refetch_forex_h4(_FOREX, 0, time_now=_NOW) == (False, 0)
