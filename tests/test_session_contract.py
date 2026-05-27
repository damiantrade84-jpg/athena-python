"""Tests for the central session contract (session_contract.py).

Covers every asset class, midnight-crossing windows, weekend handling,
naive-timestamp warnings, and legacy helper mapping.
"""

from datetime import datetime, timezone

import pytest

from session_contract import (
    QUALITY_AVOID,
    QUALITY_CLOSED,
    QUALITY_NORMAL,
    QUALITY_PREMIUM,
    QUALITY_WEAK,
    SessionClassification,
    classify_session,
    session_quality_to_legacy,
    session_to_legacy_dict,
)

# Helper: build a UTC datetime from SAST hours for readability.
# SAST = UTC+2, so SAST 09:00 = UTC 07:00.
def _utc_from_sast(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    """Create a UTC datetime given SAST wall-clock components."""
    utc_hour = hour - 2
    utc_day = day
    if utc_hour < 0:
        utc_hour += 24
        utc_day -= 1
    return datetime(year, month, utc_day, utc_hour, minute, tzinfo=timezone.utc)


# ═══════════════════════════════════════════════════════════════════════════════
# FOREX
# ═══════════════════════════════════════════════════════════════════════════════

class TestForex:
    def test_london_open_premium(self):
        # 09:00 SAST Tue = within 08:00-11:00
        sc = classify_session("EUR/USD", "forex", _utc_from_sast(2026, 5, 26, 9, 0))
        assert sc.session_quality == QUALITY_PREMIUM
        assert sc.session_name == "london_open"

    def test_london_ny_overlap_premium(self):
        # 15:45 SAST Wed
        sc = classify_session("EUR/USD", "forex", _utc_from_sast(2026, 5, 27, 15, 45))
        assert sc.session_quality == QUALITY_PREMIUM
        assert sc.session_name == "london_ny_overlap"

    def test_rollover_avoid(self):
        # 23:30 SAST Mon
        sc = classify_session("EUR/USD", "forex", _utc_from_sast(2026, 5, 25, 23, 30))
        assert sc.session_quality == QUALITY_AVOID
        assert sc.session_name == "rollover"
        assert sc.avoid_reason == "rollover_spread_risk"

    def test_asian_session_normal(self):
        # 03:00 SAST Tue
        sc = classify_session("EUR/USD", "forex", _utc_from_sast(2026, 5, 26, 3, 0))
        assert sc.session_quality == QUALITY_NORMAL
        assert sc.session_name == "asian_session"

    def test_sunday_open_avoid(self):
        # 00:30 SAST Mon (Sunday 22:30 UTC)
        sc = classify_session("EUR/USD", "forex", _utc_from_sast(2026, 5, 25, 0, 30))
        assert sc.session_quality == QUALITY_AVOID
        assert sc.session_name == "sunday_open"

    def test_late_friday_avoid(self):
        # 23:15 SAST Fri
        sc = classify_session("EUR/USD", "forex", _utc_from_sast(2026, 5, 29, 23, 15))
        assert sc.session_quality == QUALITY_AVOID
        assert sc.session_name == "late_friday"

    def test_weekend_closed(self):
        # Saturday 12:00 SAST
        sc = classify_session("EUR/USD", "forex", _utc_from_sast(2026, 5, 30, 12, 0))
        assert sc.session_quality == QUALITY_CLOSED
        assert sc.is_weekend is True

    def test_off_hours_weak(self):
        # 22:30 SAST Tue (not rollover — rollover is 23:00+)
        sc = classify_session("EUR/USD", "forex", _utc_from_sast(2026, 5, 26, 22, 30))
        assert sc.session_quality == QUALITY_WEAK
        assert sc.session_name == "late_ny"


# ═══════════════════════════════════════════════════════════════════════════════
# CRYPTO
# ═══════════════════════════════════════════════════════════════════════════════

class TestCrypto:
    def test_asia_normal(self):
        # 02:00 SAST any day
        sc = classify_session("BTC/USDT", "crypto", _utc_from_sast(2026, 5, 26, 2, 0))
        assert sc.session_quality == QUALITY_NORMAL
        assert sc.session_name == "asia_crypto"

    def test_us_open_premium(self):
        # 16:00 SAST
        sc = classify_session("BTC/USDT", "crypto", _utc_from_sast(2026, 5, 26, 16, 0))
        assert sc.session_quality == QUALITY_PREMIUM
        assert sc.session_name == "us_open_crypto"

    def test_dead_zone_weak(self):
        # 23:00 SAST
        sc = classify_session("ETH/USDT", "crypto", _utc_from_sast(2026, 5, 26, 23, 0))
        assert sc.session_quality == QUALITY_WEAK
        assert sc.session_name == "dead_zone"

    def test_dead_zone_midnight_crossing(self):
        # 00:30 SAST (next day) — still in 22:00-01:00 dead zone
        sc = classify_session("BTC/USDT", "crypto", _utc_from_sast(2026, 5, 27, 0, 30))
        assert sc.session_quality == QUALITY_WEAK
        assert sc.session_name == "dead_zone"

    def test_saturday_weekend_flag(self):
        # Saturday 16:00 SAST — crypto is open but weekend flag set
        sc = classify_session("BTC/USDT", "crypto", _utc_from_sast(2026, 5, 30, 16, 0))
        assert sc.is_weekend is True
        # Crypto is never "closed" — still classified by time window
        assert sc.session_quality == QUALITY_PREMIUM

    def test_sunday_weekend_flag(self):
        sc = classify_session("BTC/USDT", "crypto", _utc_from_sast(2026, 5, 31, 10, 0))
        assert sc.is_weekend is True
        assert sc.session_quality == QUALITY_NORMAL

    def test_eu_us_overlap_early(self):
        # 14:30 SAST — between 14:00 and 15:30 → eu_us_overlap_early
        sc = classify_session("BTC/USDT", "crypto", _utc_from_sast(2026, 5, 26, 14, 30))
        assert sc.session_quality == QUALITY_PREMIUM


# ═══════════════════════════════════════════════════════════════════════════════
# US STOCKS / ETFs
# ═══════════════════════════════════════════════════════════════════════════════

class TestUSEquity:
    def test_us_open_premium(self):
        # 15:45 SAST Tue
        sc = classify_session("AAPL", "stock", _utc_from_sast(2026, 5, 26, 15, 45))
        assert sc.session_quality == QUALITY_PREMIUM
        assert sc.session_name == "us_equity_open"

    def test_us_midday_weak(self):
        # 19:00 SAST
        sc = classify_session("AAPL", "stock", _utc_from_sast(2026, 5, 26, 19, 0))
        assert sc.session_quality == QUALITY_WEAK
        assert sc.session_name == "us_equity_midday"

    def test_us_close_premium(self):
        # 21:00 SAST
        sc = classify_session("SPY", "etf", _utc_from_sast(2026, 5, 26, 21, 0))
        assert sc.session_quality == QUALITY_PREMIUM
        assert sc.session_name == "us_equity_close"

    def test_after_hours_avoid(self):
        # 22:30 SAST
        sc = classify_session("AAPL", "stock", _utc_from_sast(2026, 5, 26, 22, 30))
        assert sc.session_quality == QUALITY_AVOID
        assert sc.avoid_reason == "after_hours_risk"

    def test_pre_market_closed(self):
        # 08:00 SAST
        sc = classify_session("AAPL", "stock", _utc_from_sast(2026, 5, 26, 8, 0))
        assert sc.session_quality == QUALITY_CLOSED

    def test_weekend_closed(self):
        sc = classify_session("AAPL", "stock", _utc_from_sast(2026, 5, 30, 16, 0))
        assert sc.session_quality == QUALITY_CLOSED
        assert sc.is_weekend is True

    def test_index_routes_us(self):
        sc = classify_session("US500", "index", _utc_from_sast(2026, 5, 26, 16, 0))
        assert sc.session_quality == QUALITY_PREMIUM

    def test_etf_bond_routes_us(self):
        sc = classify_session("TLT", "etf_bond", _utc_from_sast(2026, 5, 26, 21, 0))
        assert sc.session_quality == QUALITY_PREMIUM


# ═══════════════════════════════════════════════════════════════════════════════
# JSE
# ═══════════════════════════════════════════════════════════════════════════════

class TestJSE:
    def test_jse_open_premium(self):
        # 09:30 SAST Tue
        sc = classify_session("NPN.JO", "stock", _utc_from_sast(2026, 5, 26, 9, 30), venue="JSE")
        assert sc.session_quality == QUALITY_PREMIUM
        assert sc.session_name == "jse_open"

    def test_jse_midday_normal(self):
        sc = classify_session("NPN.JO", "stock", _utc_from_sast(2026, 5, 26, 12, 0), venue="JSE")
        assert sc.session_quality == QUALITY_NORMAL

    def test_jse_auction_weak(self):
        # 16:55 SAST
        sc = classify_session("NPN.JO", "stock", _utc_from_sast(2026, 5, 26, 16, 55), venue="JSE")
        assert sc.session_quality == QUALITY_WEAK
        assert sc.session_name == "jse_auction"

    def test_jse_closed(self):
        # 18:00 SAST
        sc = classify_session("NPN.JO", "stock", _utc_from_sast(2026, 5, 26, 18, 0), venue="JSE")
        assert sc.session_quality == QUALITY_CLOSED

    def test_jse_symbol_detection(self):
        # .JO suffix triggers JSE routing without venue hint
        sc = classify_session("SOL.JO", "stock", _utc_from_sast(2026, 5, 26, 9, 30))
        assert sc.session_name == "jse_open"

    def test_jse_weekend(self):
        sc = classify_session("NPN.JO", "stock", _utc_from_sast(2026, 5, 30, 10, 0), venue="JSE")
        assert sc.session_quality == QUALITY_CLOSED
        assert sc.is_weekend is True


# ═══════════════════════════════════════════════════════════════════════════════
# EU EQUITIES
# ═══════════════════════════════════════════════════════════════════════════════

class TestEUEquity:
    def test_eu_open_premium(self):
        # 09:30 SAST via DAX venue
        sc = classify_session("SAP.DE", "stock", _utc_from_sast(2026, 5, 26, 9, 30), venue="DAX")
        assert sc.session_quality == QUALITY_PREMIUM
        assert sc.session_name == "eu_open"

    def test_eu_midday_normal(self):
        sc = classify_session("SAP.DE", "stock", _utc_from_sast(2026, 5, 26, 13, 0))
        assert sc.session_quality == QUALITY_NORMAL
        assert sc.session_name == "eu_midday"

    def test_eu_us_overlap_premium(self):
        sc = classify_session("VOD.L", "stock", _utc_from_sast(2026, 5, 26, 16, 0))
        assert sc.session_quality == QUALITY_PREMIUM
        assert sc.session_name == "eu_us_overlap"

    def test_eu_closed(self):
        sc = classify_session("SAP.DE", "stock", _utc_from_sast(2026, 5, 26, 20, 0))
        assert sc.session_quality == QUALITY_CLOSED

    def test_eu_symbol_suffix_detection(self):
        sc = classify_session("BP.L", "stock", _utc_from_sast(2026, 5, 26, 10, 0))
        assert sc.session_name == "eu_open"

    def test_eu_weekend(self):
        sc = classify_session("SAP.DE", "stock", _utc_from_sast(2026, 5, 30, 10, 0))
        assert sc.session_quality == QUALITY_CLOSED
        assert sc.is_weekend is True


# ═══════════════════════════════════════════════════════════════════════════════
# COMMODITIES
# ═══════════════════════════════════════════════════════════════════════════════

class TestCommodity:
    def test_gold_london_premium(self):
        # 09:30 SAST
        sc = classify_session("XAUUSD", "commodity", _utc_from_sast(2026, 5, 26, 9, 30))
        assert sc.session_quality == QUALITY_PREMIUM
        assert sc.session_name == "metals_london"

    def test_gold_ny_overlap_premium(self):
        # 15:30 SAST
        sc = classify_session("XAUUSD", "commodity", _utc_from_sast(2026, 5, 26, 15, 30))
        assert sc.session_quality == QUALITY_PREMIUM
        assert sc.session_name == "metals_ny_overlap"

    def test_oil_us_premium(self):
        # 16:00 SAST
        sc = classify_session("WTI Oil", "commodity", _utc_from_sast(2026, 5, 26, 16, 0))
        assert sc.session_quality == QUALITY_PREMIUM
        assert sc.session_name == "energy_us"

    def test_commodity_maintenance_avoid(self):
        # 23:30 SAST
        sc = classify_session("XAUUSD", "commodity", _utc_from_sast(2026, 5, 26, 23, 30))
        assert sc.session_quality == QUALITY_AVOID
        assert sc.session_name == "commodity_maintenance"
        assert sc.avoid_reason == "maintenance_spread_risk"

    def test_copper_london(self):
        sc = classify_session("HG", "commodity", _utc_from_sast(2026, 5, 26, 9, 0))
        assert sc.session_quality == QUALITY_PREMIUM
        assert sc.session_name == "copper_london"

    def test_silver_premium(self):
        sc = classify_session("XAGUSD", "commodity", _utc_from_sast(2026, 5, 26, 15, 0))
        assert sc.session_quality == QUALITY_PREMIUM
        assert sc.session_name == "metals_ny_overlap"

    def test_unknown_commodity_defaults_to_metals(self):
        sc = classify_session("WHEAT", "commodity", _utc_from_sast(2026, 5, 26, 9, 0))
        assert sc.session_quality == QUALITY_PREMIUM
        assert "unknown_commodity_using_metals_default" in sc.warnings

    def test_commodity_weekend(self):
        sc = classify_session("XAUUSD", "commodity", _utc_from_sast(2026, 5, 30, 10, 0))
        assert sc.session_quality == QUALITY_CLOSED
        assert sc.is_weekend is True

    def test_commodity_off_hours_weak(self):
        # 03:00 SAST — off-hours for metals
        sc = classify_session("XAUUSD", "commodity", _utc_from_sast(2026, 5, 26, 3, 0))
        assert sc.session_quality == QUALITY_WEAK


# ═══════════════════════════════════════════════════════════════════════════════
# EDGE CASES
# ═══════════════════════════════════════════════════════════════════════════════

class TestEdgeCases:
    def test_naive_timestamp_warns(self):
        naive = datetime(2026, 5, 26, 7, 0)  # no tzinfo
        sc = classify_session("EUR/USD", "forex", naive)
        assert "naive_timestamp_treated_as_utc" in sc.warnings

    def test_iso_string_timestamp(self):
        sc = classify_session("EUR/USD", "forex", "2026-05-26T07:00:00Z")
        assert sc.session_quality == QUALITY_PREMIUM  # 07:00 UTC = 09:00 SAST → london_open

    def test_unix_timestamp(self):
        # 2026-05-26 07:00 UTC as unix ts
        dt = datetime(2026, 5, 26, 7, 0, tzinfo=timezone.utc)
        ts = dt.timestamp()
        sc = classify_session("EUR/USD", "forex", ts)
        assert sc.session_quality == QUALITY_PREMIUM

    def test_unknown_asset_class_defaults_forex(self):
        sc = classify_session("EUR/USD", "unknown_asset", _utc_from_sast(2026, 5, 26, 9, 0))
        assert "unknown_asset_class_unknown_asset_defaulting_to_forex" in sc.warnings
        assert sc.session_name == "london_open"

    def test_to_dict_has_boolean_flags(self):
        sc = classify_session("EUR/USD", "forex", _utc_from_sast(2026, 5, 26, 9, 0))
        d = sc.to_dict()
        assert d["is_premium"] is True
        assert d["is_normal"] is False
        assert d["is_weak"] is False
        assert d["is_avoid"] is False
        assert d["is_closed"] is False
        assert isinstance(d["warnings"], list)

    def test_display_name_populated(self):
        sc = classify_session("EUR/USD", "forex", _utc_from_sast(2026, 5, 26, 9, 0))
        assert sc.display_name == "London Open"

    def test_sast_time_string_format(self):
        sc = classify_session("EUR/USD", "forex", _utc_from_sast(2026, 5, 26, 9, 0))
        assert "SAST" in sc.local_time_sast
        assert "2026-05-26" in sc.local_time_sast


# ═══════════════════════════════════════════════════════════════════════════════
# LEGACY HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

class TestLegacyHelpers:
    def test_quality_to_legacy_mapping(self):
        assert session_quality_to_legacy("premium") == "high"
        assert session_quality_to_legacy("normal") == "medium"
        assert session_quality_to_legacy("weak") == "low"
        assert session_quality_to_legacy("avoid") == "low"
        assert session_quality_to_legacy("closed") == "low"

    def test_session_to_legacy_dict(self):
        sc = classify_session("EUR/USD", "forex", _utc_from_sast(2026, 5, 26, 9, 0))
        d = session_to_legacy_dict(sc)
        assert d["name"] == "London Open"
        assert d["quality"] == "high"
        assert d["color"] == "#22c55e"

    def test_legacy_dict_weak_session(self):
        sc = classify_session("EUR/USD", "forex", _utc_from_sast(2026, 5, 26, 22, 30))
        d = session_to_legacy_dict(sc)
        assert d["quality"] == "low"
        assert d["color"] == "#f59e0b"


# ═══════════════════════════════════════════════════════════════════════════════
# MIDNIGHT-CROSSING WINDOW
# ═══════════════════════════════════════════════════════════════════════════════

class TestMidnightCrossing:
    def test_crypto_dead_zone_before_midnight(self):
        sc = classify_session("BTC/USDT", "crypto", _utc_from_sast(2026, 5, 26, 23, 30))
        assert sc.session_name == "dead_zone"
        assert sc.session_quality == QUALITY_WEAK

    def test_crypto_dead_zone_after_midnight(self):
        # 00:30 SAST Wed — still in 22:00-01:00 dead zone
        sc = classify_session("BTC/USDT", "crypto", _utc_from_sast(2026, 5, 27, 0, 30))
        assert sc.session_name == "dead_zone"
        assert sc.session_quality == QUALITY_WEAK

    def test_crypto_exits_dead_zone(self):
        # 01:00 SAST — now in asia_crypto
        sc = classify_session("BTC/USDT", "crypto", _utc_from_sast(2026, 5, 27, 1, 0))
        assert sc.session_name == "asia_crypto"
        assert sc.session_quality == QUALITY_NORMAL
