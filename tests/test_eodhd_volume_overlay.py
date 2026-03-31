import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from eodhd_volume_overlay import (
    is_eodhd_volume_whitelisted,
    overlay_candle_volumes,
    resample_eodhd_volume_bars,
    supports_eodhd_volume_overlay,
)


def test_supports_eodhd_volume_overlay_only_for_target_asset_classes():
    assert supports_eodhd_volume_overlay({"type": "stock"}) is True
    assert supports_eodhd_volume_overlay({"type": "commodity"}) is True
    assert supports_eodhd_volume_overlay({"type": "index"}) is True
    assert supports_eodhd_volume_overlay({"type": "forex"}) is False
    assert supports_eodhd_volume_overlay({"type": "crypto"}) is False


def test_is_eodhd_volume_whitelisted_matches_live_audit_matrix():
    assert is_eodhd_volume_whitelisted({"type": "commodity", "symbol": "GC=F"}, "D1") is True
    assert is_eodhd_volume_whitelisted({"type": "commodity", "symbol": "GC=F"}, "H1") is False
    assert is_eodhd_volume_whitelisted({"type": "index", "symbol": "NAS100"}, "H1") is True
    assert is_eodhd_volume_whitelisted({"type": "index", "symbol": "NAS100"}, "D1") is False
    assert is_eodhd_volume_whitelisted({"type": "stock", "symbol": "AAPL.US"}, "D1") is True
    assert is_eodhd_volume_whitelisted({"type": "stock", "symbol": "AAPL.US"}, "H4") is True
    assert is_eodhd_volume_whitelisted({"type": "forex", "symbol": "EURUSD=X"}, "H1") is False


def test_overlay_candle_volumes_replaces_matching_h1_volume_only():
    base = [
        {"time": "2026-03-31T08:00:00+00:00", "open": 10, "high": 11, "low": 9, "close": 10.5, "vol": 1},
        {"time": "2026-03-31T09:00:00+00:00", "open": 11, "high": 12, "low": 10, "close": 11.5, "vol": 2},
    ]
    overlay = [
        {"time": "2026-03-31T08:00:00+00:00", "vol": 101},
        {"time": "2026-03-31T09:00:00+00:00", "vol": 202},
    ]

    merged, matched = overlay_candle_volumes(base, overlay, "H1")

    assert matched == 2
    assert merged[0]["vol"] == 101
    assert merged[1]["vol"] == 202
    assert merged[0]["open"] == 10
    assert merged[1]["close"] == 11.5


def test_overlay_candle_volumes_matches_d1_date_boundaries():
    base = [
        {"time": "2026-03-30T00:00:00+00:00", "vol": 1},
        {"time": "2026-03-31T00:00:00+00:00", "vol": 2},
    ]
    overlay = [
        {"time": "2026-03-30", "vol": 300},
        {"time": "2026-03-31", "vol": 400},
    ]

    merged, matched = overlay_candle_volumes(base, overlay, "D1")

    assert matched == 2
    assert merged[0]["vol"] == 300
    assert merged[1]["vol"] == 400


def test_resample_eodhd_volume_bars_builds_h4_from_h1():
    h1 = [
        {"time": "2026-03-31T00:00:00+00:00", "open": 10, "high": 11, "low": 9, "close": 10.1, "vol": 10},
        {"time": "2026-03-31T01:00:00+00:00", "open": 10.1, "high": 11, "low": 9.5, "close": 10.2, "vol": 20},
        {"time": "2026-03-31T02:00:00+00:00", "open": 10.2, "high": 11, "low": 9.8, "close": 10.3, "vol": 30},
        {"time": "2026-03-31T03:00:00+00:00", "open": 10.3, "high": 11.2, "low": 10, "close": 10.4, "vol": 40},
        {"time": "2026-03-31T04:00:00+00:00", "open": 10.4, "high": 11.4, "low": 10.1, "close": 10.5, "vol": 50},
    ]

    h4 = resample_eodhd_volume_bars(h1, "H4", 10)

    assert h4 is not None
    assert len(h4) == 2
    assert h4[0]["vol"] == 100
    assert h4[1]["vol"] == 50
