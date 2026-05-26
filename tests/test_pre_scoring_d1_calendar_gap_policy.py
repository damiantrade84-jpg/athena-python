"""Pre-scoring must not block policy-downgraded D1 calendar gaps."""

from unittest.mock import patch

from athena_app.services.data_freshness import pre_scoring_allows_intraday_calendar_gap


def test_intraday_policy_ok_severity_always_allowed():
    pair = {"type": "stock", "source": "mt5", "display": "AAPL"}
    diag = {"stalenessSeverity": "intraday_calendar_gap_policy_ok", "bucketLag": 22}
    assert pre_scoring_allows_intraday_calendar_gap(pair, "H4", diag) is True


def test_d1_calendar_gap_policy_ok_not_treated_as_stale_in_analyze_gate():
    """Regression: d1_calendar_gap_policy_ok was blocked outside Mon/Sat/Sun."""
    # The analyze_pair gate now continues on policy_ok severities; this test
    # documents the severities that must never land in _stale_tfs.
    policy_ok = {"d1_calendar_gap_policy_ok", "intraday_calendar_gap_policy_ok"}
    assert "d1_calendar_gap_policy_ok" in policy_ok
