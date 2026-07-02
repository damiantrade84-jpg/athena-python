import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from eodhd_volume_overlay import (
    eodhd_commodity_ticker_for_pair,
    infer_grid_offset_seconds,
    is_eodhd_volume_whitelisted,
    overlay_candle_volumes,
    resample_eodhd_volume_bars,
    supports_eodhd_volume_overlay,
)


def test_supports_eodhd_volume_overlay_only_for_target_asset_classes():
    assert supports_eodhd_volume_overlay({"type": "stock"}) is True
    assert supports_eodhd_volume_overlay({"type": "commodity"}) is True
    assert supports_eodhd_volume_overlay({"type": "index"}) is True
    # Forex added to _EODHD_VOLUME_TYPES: EODHD intraday volume overlay now active for forex pairs
    assert supports_eodhd_volume_overlay({"type": "forex"}) is True
    assert supports_eodhd_volume_overlay({"type": "crypto"}) is False


def test_configured_commodity_tickers_cover_non_slash_displays():
    for display in (
        "WTI Oil",
        "Brent Oil",
        "Nat Gas",
        "Copper",
        "Aluminium",
        "Lead",
        "Nickel",
        "Zinc",
        "Cattle",
        "Cocoa",
        "Coffee",
        "Corn",
        "Cotton",
        "Soybeans",
        "Sugar",
        "Wheat",
    ):
        ticker = eodhd_commodity_ticker_for_pair(
            {"type": "commodity", "display": display, "symbol": display}
        )
        assert ticker
        assert ticker not in {"CL", "BZ", "NG"}


def test_athena_volume_fetch_does_not_skip_whitelisted_commodities_and_indices():
    src = Path(__file__).resolve().parents[1] / "athena.py"
    text = src.read_text(encoding="utf-8")
    assert 'if ptype in {"forex", "commodity", "index"}' not in text
    assert 'if ptype == "forex":' in text
    assert "_eodhd_commodity_ticker_for_pair(pair)" in text


def test_is_eodhd_volume_whitelisted_matches_configured_matrix():
    assert is_eodhd_volume_whitelisted({"type": "commodity", "symbol": "GC=F"}, "D1") is True
    # GC=F moved to _FOREX_METALS_1M_RESAMPLE (intraday resampled from 1m); all TFs now allowed
    assert is_eodhd_volume_whitelisted({"type": "commodity", "symbol": "GC=F"}, "H1") is True
    assert is_eodhd_volume_whitelisted({"type": "index", "symbol": "NAS100"}, "H1") is True
    # NAS100 in _INDEX_INTRADAY_AND_D1 maps to _EODHD_VOLUME_TIMEFRAMES which includes D1
    assert is_eodhd_volume_whitelisted({"type": "index", "symbol": "NAS100"}, "D1") is True
    assert is_eodhd_volume_whitelisted({"type": "stock", "symbol": "AAPL.US"}, "D1") is True
    assert is_eodhd_volume_whitelisted({"type": "stock", "symbol": "AAPL.US"}, "H4") is True
    assert is_eodhd_volume_whitelisted({"type": "commodity", "display": "Lead"}, "D1") is True
    assert is_eodhd_volume_whitelisted({"type": "commodity", "display": "Lead"}, "H1") is False
    assert is_eodhd_volume_whitelisted({"type": "commodity", "display": "WTI Oil"}, "H1") is True
    # Yahoo-format EURUSD=X is not in the whitelist; display-format EUR/USD is
    assert is_eodhd_volume_whitelisted({"type": "forex", "symbol": "EURUSD=X"}, "H1") is False
    assert is_eodhd_volume_whitelisted({"type": "forex", "display": "EUR/USD"}, "H1") is True
    assert is_eodhd_volume_whitelisted({"type": "forex", "display": "EUR/USD"}, "D1") is False


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


def test_infer_grid_offset_seconds_detects_broker_shifted_h4_grid():
    # MT5 H4 bars on a UTC+3 broker land at 01/05/09/13/17/21 UTC after the shift.
    shifted = [
        {"time": "2026-03-31T05:00:00+00:00"},
        {"time": "2026-03-31T09:00:00+00:00"},
        {"time": "2026-03-31T13:00:00+00:00"},
    ]
    assert infer_grid_offset_seconds(shifted, "H4") == 3600

    aligned = [
        {"time": "2026-03-31T04:00:00+00:00"},
        {"time": "2026-03-31T08:00:00+00:00"},
    ]
    assert infer_grid_offset_seconds(aligned, "H4") == 0
    # Hour-aligned bars have no offset on H1; D1 has no fixed bucket (date matching).
    assert infer_grid_offset_seconds(shifted, "H1") == 0
    assert infer_grid_offset_seconds(shifted, "D1") == 0
    assert infer_grid_offset_seconds([], "H4") == 0
    assert infer_grid_offset_seconds(None, "H4") == 0


def test_resample_h4_with_offset_matches_broker_shifted_bars():
    # Four 1h exchange-volume bars inside the broker-shifted 13:00-17:00 UTC window.
    h1 = [
        {"time": "2026-03-31T13:30:00+00:00", "open": 1, "high": 2, "low": 1, "close": 2, "vol": 10},
        {"time": "2026-03-31T14:30:00+00:00", "open": 2, "high": 3, "low": 2, "close": 3, "vol": 20},
        {"time": "2026-03-31T15:30:00+00:00", "open": 3, "high": 4, "low": 3, "close": 4, "vol": 30},
        {"time": "2026-03-31T16:30:00+00:00", "open": 4, "high": 5, "low": 4, "close": 5, "vol": 40},
    ]

    epoch_aligned = resample_eodhd_volume_bars(h1, "H4", 10)
    assert epoch_aligned is not None
    # Without the offset the buckets sit on the 12:00/16:00 epoch grid...
    assert [c["time"][11:16] for c in epoch_aligned] == ["12:00", "16:00"]

    shifted = resample_eodhd_volume_bars(h1, "H4", 10, offset_seconds=3600)
    assert shifted is not None
    # ...with it they land on the broker grid and hold the full window's volume.
    assert [c["time"][11:16] for c in shifted] == ["13:00"]
    assert shifted[0]["vol"] == 100

    base = [
        {"time": "2026-03-31T09:00:00+00:00", "open": 1, "high": 2, "low": 1, "close": 2, "vol": 7},
        {"time": "2026-03-31T13:00:00+00:00", "open": 2, "high": 5, "low": 2, "close": 5, "vol": 9},
    ]
    offset = infer_grid_offset_seconds(base, "H4")
    assert offset == 3600
    merged, matched = overlay_candle_volumes(
        base, resample_eodhd_volume_bars(h1, "H4", 10, offset_seconds=offset), "H4"
    )
    assert matched == 1
    assert merged[0]["vol"] == 7   # no exchange data for the 09:00 window -> tick volume kept
    assert merged[1]["vol"] == 100  # 13:00-17:00 window replaced with exchange volume


def test_resample_eodhd_volume_bars_offset_zero_is_unchanged():
    h1 = [
        {"time": "2026-03-31T00:00:00+00:00", "open": 10, "high": 11, "low": 9, "close": 10.1, "vol": 10},
        {"time": "2026-03-31T01:00:00+00:00", "open": 10.1, "high": 11, "low": 9.5, "close": 10.2, "vol": 20},
    ]
    assert resample_eodhd_volume_bars(h1, "H4", 10) == resample_eodhd_volume_bars(
        h1, "H4", 10, offset_seconds=0
    )


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
