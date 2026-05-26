"""Pre-scoring intraday calendar gap policy for US equities."""

from datetime import datetime, timezone
from unittest.mock import patch

from athena_app.services.data_freshness import pre_scoring_allows_intraday_calendar_gap
from config import CONFIG


def test_intraday_calendar_gap_policy_ok_for_stale_h4_within_grace():
    pair = {"type": "stock", "source": "mt5", "display": "AAPL"}
    diag = {
        "stalenessSeverity": "intraday_calendar_gap_policy_ok",
        "bucketLag": 22,
    }
    assert pre_scoring_allows_intraday_calendar_gap(pair, "H4", diag) is True


def test_stock_off_hours_allows_stale_multi_bucket():
    pair = {"type": "stock", "source": "mt5", "display": "AAPL"}
    diag = {"stalenessSeverity": "stale_multi_bucket", "bucketLag": 22}
    off_hours = datetime(2026, 5, 26, 8, 0, tzinfo=timezone.utc)
    with patch("athena_app.services.data_freshness._us_equity_off_hours_now", return_value=True):
        assert pre_scoring_allows_intraday_calendar_gap(pair, "H4", diag) is True


def test_market_state_h4_stale_multi_within_grace_is_policy_ok():
    """Synthetic lag within MT5_INTRADAY_CALENDAR_GAP_GRACE_BUCKETS → policy_ok."""
    pair = {"type": "stock", "source": "mt5", "display": "AAPL"}
    grace = {"H4": 30, "H1": 120}
    with patch.dict(CONFIG, {"MT5_INTRADAY_CALENDAR_GAP_GRACE_BUCKETS": grace}, clear=False):
        diag = {
            "stalenessSeverity": "stale_multi_bucket",
            "bucketLag": 22,
        }
        assert pre_scoring_allows_intraday_calendar_gap(pair, "H4", diag) is True


def _epoch(iso_text: str) -> float:
    return datetime.fromisoformat(iso_text.replace("Z", "+00:00")).timestamp()
