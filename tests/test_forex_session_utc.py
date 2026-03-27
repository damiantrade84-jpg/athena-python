"""Tests for UTC-based forex session handling in forex_scoring.py."""

import os
import sys
from datetime import datetime as real_datetime, timezone
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config import CONFIG
from forex_scoring import _in_session, _local_to_utc_hour


class TestForexSessionUtc:
    @patch("datetime.datetime")
    def test_local_to_utc_hour_uses_direct_utc_clock(self, mock_datetime):
        mock_datetime.now.return_value = real_datetime(2026, 3, 27, 14, 30, tzinfo=timezone.utc)
        assert _local_to_utc_hour() == 14

    @patch("datetime.datetime")
    def test_server_tz_offset_does_not_change_returned_hour(self, mock_datetime):
        mock_datetime.now.return_value = real_datetime(2026, 3, 27, 14, 30, tzinfo=timezone.utc)
        original = CONFIG.get("SERVER_TZ_OFFSET_HOURS")
        try:
            CONFIG["SERVER_TZ_OFFSET_HOURS"] = 9
            assert _local_to_utc_hour() == 14
            CONFIG["SERVER_TZ_OFFSET_HOURS"] = -7
            assert _local_to_utc_hour() == 14
        finally:
            CONFIG["SERVER_TZ_OFFSET_HOURS"] = original

    def test_in_session_true_for_major_pair_at_london_ny_overlap(self):
        assert _in_session(14, "EUR/USD") is True

    def test_in_session_false_for_major_pair_in_asian_session(self):
        assert _in_session(2, "EUR/USD") is False
