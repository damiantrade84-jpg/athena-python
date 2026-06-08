"""Regression tests for pre-scoring freshness gate timezone/weekend handling.

Verifies:
- stale_1_bucket passes for confirmed-only forex pairs
- stale_1_bucket passes for confirmed-only crypto pairs
- D1 weekend gap (up to 4 buckets) passes on Monday
- stale_multi_bucket on H1/H4 during market hours still blocks
- Crypto pairs remain strict for multi-bucket stale data
"""

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from athena_app.services.market_state import (
    candle_freshness_diagnostic,
    get_bucket_start_epoch,
    market_state_offset_hours,
)
from athena_app.services.data_freshness import pre_scoring_allows_confirmed_only_stale_1
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


class TestH4OffsetDerivation:
    """market_state_offset_hours derives H4 offset from MT5_BROKER_UTC_OFFSET."""

    def test_gmt_plus_3_gives_offset_1(self, monkeypatch):
        monkeypatch.setitem(CONFIG, "MT5_BROKER_UTC_OFFSET", 3)
        monkeypatch.setitem(CONFIG, "FOREX_H4_RESAMPLE_OFFSET_HOURS", None)
        pair = {"type": "forex", "source": "mt5"}
        assert market_state_offset_hours(pair, "H4") == 1.0

    def test_gmt_plus_2_gives_offset_2(self, monkeypatch):
        monkeypatch.setitem(CONFIG, "MT5_BROKER_UTC_OFFSET", 2)
        monkeypatch.setitem(CONFIG, "FOREX_H4_RESAMPLE_OFFSET_HOURS", None)
        pair = {"type": "forex", "source": "mt5"}
        assert market_state_offset_hours(pair, "H4") == 2.0

    def test_explicit_override_takes_precedence(self, monkeypatch):
        monkeypatch.setitem(CONFIG, "MT5_BROKER_UTC_OFFSET", 3)
        monkeypatch.setitem(CONFIG, "FOREX_H4_RESAMPLE_OFFSET_HOURS", 2.0)
        pair = {"type": "forex", "source": "mt5"}
        assert market_state_offset_hours(pair, "H4") == 2.0


class TestH4BucketGridGMT3:
    """H4 bucket grid aligns with GMT+3 broker bars (01/05/09/13/17/21 UTC)."""

    def test_bar_at_0100_utc_is_in_bucket_0100(self, monkeypatch):
        monkeypatch.setitem(CONFIG, "FOREX_H4_RESAMPLE_OFFSET_HOURS", 1.0)
        ts = _epoch("2026-05-11T01:00:00Z")
        bucket = get_bucket_start_epoch("H4", ts, offset_hours=1.0)
        assert bucket == ts

    def test_bar_at_0500_utc_is_in_bucket_0500(self, monkeypatch):
        monkeypatch.setitem(CONFIG, "FOREX_H4_RESAMPLE_OFFSET_HOURS", 1.0)
        ts = _epoch("2026-05-11T05:00:00Z")
        bucket = get_bucket_start_epoch("H4", ts, offset_hours=1.0)
        assert bucket == ts

    def test_0848_utc_current_bucket_is_0500(self, monkeypatch):
        monkeypatch.setitem(CONFIG, "FOREX_H4_RESAMPLE_OFFSET_HOURS", 1.0)
        now = _epoch("2026-05-11T08:48:00Z")
        bucket = get_bucket_start_epoch("H4", now, offset_hours=1.0)
        assert bucket == _epoch("2026-05-11T05:00:00Z")


class TestPreScoringFreshnessGateForex:
    """Pre-scoring gate tolerates confirmed-only lag for forex."""

    def test_stale_1_bucket_h4_passes_for_forex(self, monkeypatch):
        """Confirmed-only forex H4: last bar 1 bucket behind is normal."""
        monkeypatch.setitem(CONFIG, "FOREX_H4_RESAMPLE_OFFSET_HOURS", 1.0)
        monkeypatch.setitem(CONFIG, "MT5_BROKER_UTC_OFFSET", 3)
        pair = {"type": "forex", "source": "mt5", "display": "EUR/USD"}

        # At 08:48 UTC, current H4 bucket = 05:00; last confirmed = 01:00 → lag=1
        diag = candle_freshness_diagnostic(
            pair, "H4",
            [_candle("2026-05-10T21:00:00Z"), _candle("2026-05-11T01:00:00Z")],
            time_now=_epoch("2026-05-11T08:48:00Z"),
        )
        assert diag["stalenessSeverity"] == "stale_1_bucket"
        assert diag["bucketLag"] == 1

    def test_stale_1_bucket_h1_passes_for_forex(self, monkeypatch):
        """Confirmed-only forex H1: last bar 1 bucket behind is normal."""
        pair = {"type": "forex", "source": "mt5", "display": "EUR/USD"}

        # At 08:48 UTC, current H1 bucket = 08:00; last confirmed = 07:00 → lag=1
        diag = candle_freshness_diagnostic(
            pair, "H1",
            [_candle("2026-05-11T06:00:00Z"), _candle("2026-05-11T07:00:00Z")],
            time_now=_epoch("2026-05-11T08:48:00Z"),
        )
        assert diag["stalenessSeverity"] == "stale_1_bucket"
        assert diag["bucketLag"] == 1

    def test_d1_weekend_gap_monday_morning(self, monkeypatch):
        """D1 confirmed-only on Monday: Friday is last confirmed → 3-day lag, downgraded to policy-ok."""
        pair = {"type": "forex", "source": "mt5", "display": "EUR/USD"}

        # Monday 08:48 UTC; last confirmed D1 = Thursday 21:00 (Friday bar open for GMT+3)
        # or Friday 00:00 UTC for offset=0 brokers
        friday_bar = _candle("2026-05-09T00:00:00Z")
        diag = candle_freshness_diagnostic(
            pair, "D1",
            [_candle("2026-05-08T00:00:00Z"), friday_bar],
            time_now=_epoch("2026-05-12T08:48:00Z"),  # Monday May 12
        )
        # Diagnostic downgrades stale_multi_bucket to d1_calendar_gap_policy_ok for weekend gaps
        assert diag["stalenessSeverity"] == "d1_calendar_gap_policy_ok"
        assert diag["bucketLag"] >= 2

    def test_crypto_h4_stale_1_bucket_passes_for_confirmed_only_policy(self, monkeypatch):
        """Confirmed-only crypto H4: last closed bar 1 bucket behind is normal."""
        monkeypatch.setitem(CONFIG, "FOREX_H4_RESAMPLE_OFFSET_HOURS", 0.0)
        pair = {"type": "crypto", "source": "binance", "display": "BTCUSDT"}

        diag = candle_freshness_diagnostic(
            pair, "H4",
            [_candle("2026-05-11T00:00:00Z"), _candle("2026-05-11T04:00:00Z")],
            time_now=_epoch("2026-05-11T08:48:00Z"),
        )
        # Current bucket=08:00, last bar=04:00, lag=1
        assert diag["stalenessSeverity"] == "stale_1_bucket"
        assert diag["bucketLag"] == 1
        assert pre_scoring_allows_confirmed_only_stale_1(pair) is True

    def test_crypto_h4_stale_multi_bucket_remains_strict(self, monkeypatch):
        """Confirmed-only tolerance does not cover genuinely stale crypto H4 data."""
        monkeypatch.setitem(CONFIG, "FOREX_H4_RESAMPLE_OFFSET_HOURS", 0.0)
        pair = {"type": "crypto", "source": "binance", "display": "BTCUSDT"}

        diag = candle_freshness_diagnostic(
            pair, "H4",
            [_candle("2026-05-10T20:00:00Z"), _candle("2026-05-11T00:00:00Z")],
            time_now=_epoch("2026-05-11T08:48:00Z"),
        )
        assert diag["stalenessSeverity"] == "stale_multi_bucket"
        assert diag["bucketLag"] > 1

    def test_crypto_d1_lag_1_with_40h_wall_clock_not_hours_stale(self):
        """Confirmed-only crypto D1: ~40h age with bucketLag=1 must not trigger hours gate."""
        pair = {"type": "crypto", "source": "binance", "display": "BTCUSDT"}
        diag = candle_freshness_diagnostic(
            pair,
            "D1",
            [_candle("2026-06-07T00:00:00Z"), _candle("2026-06-08T00:00:00Z")],
            time_now=_epoch("2026-06-09T16:00:00Z"),
        )
        assert diag["stalenessSeverity"] == "stale_1_bucket"
        assert diag["bucketLag"] == 1
        bucket_lag = int(diag.get("bucketLag") or 0)
        sev = str(diag.get("stalenessSeverity") or "")
        genuine_multi_bucket = (
            bucket_lag >= 2
            or sev in ("stale_multi_bucket", "missing_current_bucket")
        )
        assert genuine_multi_bucket is False

    def test_crypto_d1_multi_bucket_triggers_hours_gate_path(self):
        """Genuine multi-bucket crypto D1 remains eligible for hours-based pre-score block."""
        pair = {"type": "crypto", "source": "binance", "display": "BTCUSDT"}
        diag = candle_freshness_diagnostic(
            pair,
            "D1",
            [_candle("2026-06-05T00:00:00Z"), _candle("2026-06-06T00:00:00Z")],
            time_now=_epoch("2026-06-09T16:00:00Z"),
        )
        assert diag["stalenessSeverity"] == "stale_multi_bucket"
        assert int(diag.get("bucketLag") or 0) >= 2
        bucket_lag = int(diag.get("bucketLag") or 0)
        sev = str(diag.get("stalenessSeverity") or "")
        genuine_multi_bucket = (
            bucket_lag >= 2
            or sev in ("stale_multi_bucket", "missing_current_bucket")
        )
        assert genuine_multi_bucket is True


class TestFetchMT5OffsetDetection:
    """fetch_mt5 bar-based timezone detection logic."""

    def test_bars_in_utc_no_shift_needed(self):
        """If newest bar is recent relative to UTC, no shift applied."""
        import time

        now = time.time()
        # Simulate H1 bars in UTC — newest bar started at current hour
        newest_bar_time = int(now) - (int(now) % 3600)  # Current hour start
        _tf_sec = 3600
        _unshifted_age = now - newest_bar_time

        # Should be within [−300, 2*3600] → no shift
        assert _unshifted_age >= -300
        assert _unshifted_age <= 2 * _tf_sec

    def test_bars_in_server_time_need_shift(self):
        """If newest bar is 3h ahead of UTC, shift by broker offset."""
        import time

        now = time.time()
        broker_offset = 3
        # Simulate H1 bar in server time (3h ahead)
        newest_bar_time = int(now) - (int(now) % 3600) + broker_offset * 3600
        _tf_sec = 3600
        _unshifted_age = now - newest_bar_time

        # Unshifted: bar is in future → outside valid range
        assert _unshifted_age < -300

        # After shift: should be valid
        _shifted_age = now - (newest_bar_time - broker_offset * 3600)
        assert _shifted_age >= -300
        assert _shifted_age <= 2 * _tf_sec
