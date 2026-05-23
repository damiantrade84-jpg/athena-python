"""Tests for Engine D session context resolver."""

from __future__ import annotations

from datetime import datetime, timezone

from ai_scalp_review.session_context import resolve_engine_d_session_context


def test_ny_midday_resolves_compression_window():
    # 2026-01-15 18:00 UTC = 13:00 America/New_York (EST) -> NY_MIDDAY
    when = datetime(2026, 1, 15, 18, 0, tzinfo=timezone.utc)
    ctx = resolve_engine_d_session_context(
        when=when,
        asset_type="forex",
        signal={"market_state": "balance", "atr": 1.0, "price": 100.0},
    )
    assert ctx["currentSession"] == "NY_MIDDAY"
    assert ctx["deliveryWindow"] == "COMPRESSION_WINDOW"
    assert ctx["sessionConvictionAdjustment"] == "DOWNGRADE"
    assert ctx["sessionQuality"] == "CHOP_RISK"


def test_ny_open_clean_delivery_window():
    # 2026-01-15 15:00 UTC = 10:00 America/New_York -> NY_OPEN
    when = datetime(2026, 1, 15, 15, 0, tzinfo=timezone.utc)
    ctx = resolve_engine_d_session_context(
        when=when,
        asset_type="forex",
        signal={"market_state": "imbalance", "atr": 0.2, "price": 100.0},
    )
    assert ctx["currentSession"] == "NY_OPEN"
    assert ctx["deliveryWindow"] == "CLEAN_EXPANSION_WINDOW"
    assert ctx["preferredModel"] == "NY_TREND_SQUEEZE"


def test_crypto_asia_session():
    when = datetime(2026, 1, 15, 5, 0, tzinfo=timezone.utc)
    ctx = resolve_engine_d_session_context(when=when, asset_type="crypto", signal={})
    assert ctx["currentSession"] == "ASIA"


def test_profit_lock_active_late_session_risk():
    when = datetime(2026, 1, 15, 15, 0, tzinfo=timezone.utc)
    ctx = resolve_engine_d_session_context(
        when=when,
        asset_type="forex",
        signal={"market_state": "imbalance"},
        session_risk_state={"net_r_today": 2.5, "size_cut_active": True},
    )
    assert ctx["profitLockActive"] is True
    assert ctx["deliveryWindow"] == "LATE_SESSION_RISK"
    assert ctx["sessionConvictionAdjustment"] == "HARD_REJECT"
