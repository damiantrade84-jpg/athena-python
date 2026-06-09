"""Unit tests for MT5 bar timestamp inference (timezone vs stale-feed guard)."""

from athena_app.services.mt5_time_alignment import infer_mt5_rates_time_shift_seconds


def test_infer_recent_bar_zero_shift_utc_assumed():
    now = 1_704_000_000.0
    newest = now - 1800.0  # 30m-old H1 bar
    off, tag = infer_mt5_rates_time_shift_seconds("H1", now, newest, broker_offset_hours=3, tick_epoch=None)
    assert off == 0 and tag == "utc_assumed_recent"


def test_infer_broker_offset_path_future_stamped_native_bar():
    """Some brokers stamp OHLC naive times slightly ahead vs Python wall clock."""
    now = 1_704_000_000.0
    newest = now + 5000.0
    off, tag = infer_mt5_rates_time_shift_seconds(
        "H1", now, newest, broker_offset_hours=3, tick_epoch=None
    )
    assert tag == "broker_offset_applied"
    assert off == int(3 * 3600)


def test_infer_stale_feed_skips_tick_tz():
    """Illiquid symbols: refuse tick TZ that previously produced bogus ±60h offsets."""
    now = 1_704_000_000.0
    newest = now - (400_000.0)  # > ~4.6d stale
    off, tag = infer_mt5_rates_time_shift_seconds(
        "H1",
        now,
        newest,
        broker_offset_hours=3,
        tick_epoch=now + 7777.0,  # would have produced wild tick delta
        stale_unshifted_age_sec=36 * 3600,
    )
    assert off == 0 and tag == "stale_feed_skip_tick_tz"


def test_infer_tick_tz_clamped_out_of_band():
    now = 1_704_000_000.0
    newest = now - 9000.0  # > 2h lag on H1 — not utc_assumed; broker path won't rescue
    weird_tick = now + int(20 * 3600)
    off, tag = infer_mt5_rates_time_shift_seconds(
        "H1",
        now,
        newest,
        broker_offset_hours=99,
        tick_epoch=float(weird_tick),
        stale_unshifted_age_sec=999999.0,
    )
    assert off == 0 and tag == "tick_tz_out_of_band"


def test_d1_returns_zero_without_shift_tag():
    off, tag = infer_mt5_rates_time_shift_seconds("D1", 123.0, 100.0, 3, None)
    assert off == 0 and tag == "d1_shift_not_applied_in_fetch_mt5"


def test_tick_disambiguates_broker_stamped_stale_bar_in_recent_window():
    """Observed 2026-06-09: broker-stamped (+3h) H4 feed lagging ~1 bucket lands in the
    unshifted 'recent' window and used to be relabeled as fresh UTC (utc_assumed_recent),
    masking staleness from the freshness gates. With a live tick clock confirming the
    +3h broker offset, the broker shift must win."""
    now = 1_781_034_014.0  # 2026-06-09T19:40:14Z
    newest = 1_781_020_800.0  # raw broker label 16:00Z (= 13:00 UTC canonical), age ~3.7h
    off, tag = infer_mt5_rates_time_shift_seconds(
        "H4",
        now,
        newest,
        broker_offset_hours=3,
        tick_epoch=now + 3 * 3600.0,  # broker tick clock runs +3h vs wall clock
    )
    assert tag == "broker_offset_applied"
    assert off == int(3 * 3600)


def test_genuine_utc_feed_keeps_utc_assumed_despite_tick():
    """A genuinely UTC-stamped fresh feed has tick delta ~0 vs wall clock and must keep
    the unshifted path even when a broker offset is configured."""
    now = 1_781_034_014.0
    newest = now - 1800.0  # 30m-old H1 bar on UTC stamps
    off, tag = infer_mt5_rates_time_shift_seconds(
        "H1", now, newest, broker_offset_hours=3, tick_epoch=now - 20.0
    )
    assert off == 0 and tag == "utc_assumed_recent"


def test_tick_delta_not_matching_offset_keeps_utc_assumed():
    """Tick delta that does not round to the configured broker offset (e.g. stale tick
    on an illiquid symbol) must not trigger the broker shift in the recent window."""
    now = 1_781_034_014.0
    newest = now - 1800.0
    off, tag = infer_mt5_rates_time_shift_seconds(
        "H1", now, newest, broker_offset_hours=3, tick_epoch=now + 1.4 * 3600.0
    )
    assert off == 0 and tag == "utc_assumed_recent"


def test_recent_window_without_tick_keeps_utc_assumed():
    """No tick available -> no disambiguation evidence -> keep prior behavior."""
    now = 1_781_034_014.0
    newest = now - 1800.0
    off, tag = infer_mt5_rates_time_shift_seconds(
        "H1", now, newest, broker_offset_hours=3, tick_epoch=None
    )
    assert off == 0 and tag == "utc_assumed_recent"
