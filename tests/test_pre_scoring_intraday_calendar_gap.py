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


def test_forex_weekend_h4_stale_multi_within_grace_is_policy_ok():
    """Forex H4 lag during weekend closure is expected, not a live feed fault."""
    from athena_app.services.market_state import candle_freshness_diagnostic

    pair = {"type": "forex", "source": "mt5", "display": "NZD/USD"}
    saturday = _epoch("2026-06-13T19:00:00Z")
    last_h4 = {
        "time": "2026-06-12T21:00:00Z",
        "open": 0.6,
        "high": 0.61,
        "low": 0.59,
        "close": 0.605,
    }
    with patch.dict(CONFIG, {"FOREX_H4_RESAMPLE_OFFSET_HOURS": 1.0}, clear=False):
        diag = candle_freshness_diagnostic(
            pair,
            "H4",
            [last_h4],
            time_now=saturday,
        )
    assert diag["stalenessSeverity"] == "intraday_calendar_gap_policy_ok"
    assert pre_scoring_allows_intraday_calendar_gap(pair, "H4", diag) is True


def test_fetch_meta_cache_path_applies_calendar_gap_policy():
    """Cache fetch meta must not keep raw stale_multi_bucket after calendar-gap policy."""
    from athena_app.services.data_freshness import check_live_candle_consistency

    pair = {"type": "forex", "source": "mt5", "display": "NZD/USD"}
    saturday = _epoch("2026-06-13T19:00:00Z")
    friday_d1 = int(datetime(2026, 6, 12, 0, 0, tzinfo=timezone.utc).timestamp())
    bar = {"time": friday_d1, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0}
    result = check_live_candle_consistency(
        pair,
        "D1",
        {
            "engine_a": [bar],
            "cache": {
                "stalenessSeverity": "stale_multi_bucket",
                "bucketLag": 2,
                "expectedCurrentBucketEpoch": int(
                    datetime(2026, 6, 14, 0, 0, tzinfo=timezone.utc).timestamp()
                ),
            },
        },
        time_now=saturday,
    )
    assert result["status"] != "ERROR_STALE_MULTI_BUCKET"
    assert result["paths"]["cache"]["stalenessSeverity"] == "d1_calendar_gap_policy_ok"


def _epoch(iso_text: str) -> float:
    return datetime.fromisoformat(iso_text.replace("Z", "+00:00")).timestamp()
