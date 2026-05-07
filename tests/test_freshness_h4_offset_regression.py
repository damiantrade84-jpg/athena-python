"""Regression: MT5 H4 offset alignment, cache metadata, and YAML duplicate keys."""

from pathlib import Path

import pytest
import yaml
from athena_app.services.data_freshness import build_live_feed_diagnostic, check_live_candle_consistency
from athena_app.services.market_state import candle_freshness_diagnostic, get_bucket_start_epoch
from config import CONFIG, scan_duplicate_top_level_yaml_keys


def test_scan_duplicate_top_level_detects_repeated_fx_key():
    sample = (
        "A: 1\n"
        "B: 2\n"
        "FOREX_H4_RESAMPLE_OFFSET_HOURS: 1.0\n"
        "C: 3\n"
        "FOREX_H4_RESAMPLE_OFFSET_HOURS: 2.0\n"
    )
    dupes = scan_duplicate_top_level_yaml_keys(sample)
    assert "FOREX_H4_RESAMPLE_OFFSET_HOURS" in dupes
    assert len(dupes["FOREX_H4_RESAMPLE_OFFSET_HOURS"]) == 2


def test_production_config_yaml_has_no_root_duplicate_fx_h4():
    cfg_path = Path(__file__).resolve().parents[1] / "config.yaml"
    text = cfg_path.read_text(encoding="utf-8")
    dupes = scan_duplicate_top_level_yaml_keys(text)
    assert "FOREX_H4_RESAMPLE_OFFSET_HOURS" not in dupes, f"duplicates: {dupes}"


def test_pyyaml_loads_single_fx_h4_value():
    cfg_path = Path(__file__).resolve().parents[1] / "config.yaml"
    with open(cfg_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    v = data.get("FOREX_H4_RESAMPLE_OFFSET_HOURS")
    assert v == 2.0
    # Runtime CONFIG after merge
    assert float(CONFIG.get("FOREX_H4_RESAMPLE_OFFSET_HOURS", 0.0) or 0.0) == 2.0


def test_fetch_meta_annotates_offset_hours_for_mt5_h4(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from candles_cache import _annotate_fetch_meta_with_bar_freshness

    meta: dict = {}
    candles = [
        {
            "time": "2026-04-24T02:00:00Z",
            "open": 1.0,
            "high": 1.0,
            "low": 1.0,
            "close": 1.0,
        }
    ]
    out = _annotate_fetch_meta_with_bar_freshness(
        meta, candles, "H4", offset_hours=2.0, now=1_000_000.0, live_feed=False
    )
    assert out["offsetHours"] == 2.0
    assert "expectedCurrentBucketEpoch" in out


def test_candle_freshness_and_cache_meta_h4_offset_aligned_mt5_commodity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MT5 H4 (non-forex) must use the same H4 offset as market_state, not 0.0 from cache bug."""
    monkeypatch.setitem(CONFIG, "FOREX_H4_RESAMPLE_OFFSET_HOURS", 2.0)
    pair_commodity = {
        "type": "commodity",
        "display": "XAU/USD",
        "symbol": "XAUUSD",
        "source": "mt5",
    }
    d = candle_freshness_diagnostic(
        pair_commodity,
        "H4",
        [
            {
                "time": "2026-04-24T02:00:00Z",
                "open": 1.0,
                "high": 1.0,
                "low": 1.0,
                "close": 1.0,
            }
        ],
        time_now=1_000_000.0,
    )
    assert d["offsetHours"] == 2.0
    from candles_cache import _annotate_fetch_meta_with_bar_freshness

    meta2: dict = {}
    _annotate_fetch_meta_with_bar_freshness(
        meta2,
        [
            {
                "time": "2026-04-24T02:00:00Z",
                "open": 1.0,
                "high": 1.0,
                "low": 1.0,
                "close": 1.0,
            }
        ],
        "H4",
        offset_hours=2.0,
        now=1_000_000.0,
    )
    assert meta2.get("offsetHours") == 2.0
    # Same grid as get_bucket_start_epoch with offset
    t0 = 1_000_000.0
    assert d["expectedCurrentBucketEpoch"] == get_bucket_start_epoch("H4", t0, 2.0)


def test_check_consistency_no_error_offset_when_cache_includes_offset_hours(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Simulate fetch_meta (cache path) with offsetHours + expected bucket = engine paths."""
    monkeypatch.setitem(CONFIG, "FOREX_H4_RESAMPLE_OFFSET_HOURS", 1.0)
    pair = {"type": "forex", "display": "EUR/USD", "symbol": "EURUSD", "source": "mt5"}
    t = 1713868200.0
    d_engine = build_live_feed_diagnostic(
        pair,
        "H4",
        [
            {
                "time": "2026-04-24T01:00:00Z",
                "open": 1.0,
                "high": 1.0,
                "low": 1.0,
                "close": 1.0,
            }
        ],
        fetch_meta={"cacheBypass": True, "upstream": "mt5"},
        time_now=t,
    )
    cache_row = {**_get_candle_meta_like_fetch(d_engine), "offsetHours": 1.0}
    r = check_live_candle_consistency(
        pair,
        "H4",
        {
            "raw_provider": d_engine,
            "cache": cache_row,
            "engine_a": [],
            "engine_b": [],
        },
        time_now=t,
    )
    assert r["status"] != "ERROR_OFFSET_MISMATCH", r.get("reason", r)


def _get_candle_meta_like_fetch(d: dict) -> dict:
    """Shape like candles_cache _annotate (subset)."""
    return {
        "expectedCurrentBucketEpoch": d["expectedCurrentBucketEpoch"],
        "stalenessSeverity": d.get("stalenessSeverity"),
        "bucketLag": d.get("bucketLag"),
    }


def test_offset_mismatch_when_paths_use_different_h4_offset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(CONFIG, "FOREX_H4_RESAMPLE_OFFSET_HOURS", 1.0)
    pair = {"type": "forex", "display": "EUR/USD", "source": "mt5"}
    t = 1713868200.0
    good = build_live_feed_diagnostic(
        pair,
        "H4",
        [{"time": "2026-04-24T01:00:00Z", "open": 1, "high": 1, "low": 1, "close": 1}],
        time_now=t,
    )
    bad_cache = {**_get_candle_meta_like_fetch(good), "offsetHours": 0.0}
    r = check_live_candle_consistency(
        pair,
        "H4",
        {
            "raw_provider": good,
            "cache": bad_cache,
        },
        time_now=t,
    )
    assert r["status"] == "ERROR_OFFSET_MISMATCH"


def test_confirmed_only_h4_one_bucket_lag_status(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_data_freshness import _epoch
    from tests.test_data_freshness import _diag as drow

    monkeypatch.setitem(CONFIG, "FOREX_H4_RESAMPLE_OFFSET_HOURS", 1.0)
    pair = {"type": "forex", "display": "EUR/USD", "symbol": "EURUSD", "source": "mt5"}
    t0 = _epoch("2026-04-24T06:30:00Z")
    r = check_live_candle_consistency(
        pair,
        "H4",
        {
            "raw_provider": drow("H4", "2026-04-24T05:00:00Z", "2026-04-24T05:00:00Z", offset=1.0),
            "engine_a": drow(
                "H4",
                "2026-04-24T01:00:00Z",
                "2026-04-24T05:00:00Z",
                offset=1.0,
                severity="stale_1_bucket",
                lag=1,
            ),
            "engine_b": drow(
                "H4",
                "2026-04-24T01:00:00Z",
                "2026-04-24T05:00:00Z",
                offset=1.0,
                severity="stale_1_bucket",
                lag=1,
            ),
            "scanner": drow(
                "H4",
                "2026-04-24T01:00:00Z",
                "2026-04-24T05:00:00Z",
                offset=1.0,
                severity="stale_1_bucket",
                lag=1,
            ),
        },
        time_now=t0,
    )
    assert r["status"] == "CONFIRMED_ONLY_OK"


def test_confirmed_only_ok_when_raw_provider_is_latest_closed_bucket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MT5 may return only the latest closed H4 bar; that is policy-ok, not scan-stale."""
    from tests.test_data_freshness import _epoch
    from tests.test_data_freshness import _diag as drow

    monkeypatch.setitem(CONFIG, "FOREX_H4_RESAMPLE_OFFSET_HOURS", 1.0)
    pair = {"type": "forex", "display": "EUR/USD", "symbol": "EURUSD", "source": "mt5"}
    t0 = _epoch("2026-04-24T06:30:00Z")
    r = check_live_candle_consistency(
        pair,
        "H4",
        {
            # No forming/current 05:00 bucket from provider; latest closed bucket is 01:00.
            "raw_provider": drow(
                "H4",
                "2026-04-24T01:00:00Z",
                "2026-04-24T05:00:00Z",
                offset=1.0,
                severity="stale_1_bucket",
                lag=1,
            ),
            "cache": drow(
                "H4",
                "2026-04-24T01:00:00Z",
                "2026-04-24T05:00:00Z",
                offset=1.0,
                severity="stale_1_bucket",
                lag=1,
            ),
            "engine_a": drow(
                "H4",
                "2026-04-24T01:00:00Z",
                "2026-04-24T05:00:00Z",
                offset=1.0,
                severity="stale_1_bucket",
                lag=1,
            ),
            "engine_b": drow(
                "H4",
                "2026-04-24T01:00:00Z",
                "2026-04-24T05:00:00Z",
                offset=1.0,
                severity="stale_1_bucket",
                lag=1,
            ),
        },
        time_now=t0,
    )
    assert r["status"] == "CONFIRMED_ONLY_OK"


def test_confirmed_only_consistency_suppresses_fetch_meta_stale_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from athena_app.services.data_freshness import evaluate_execution_data_freshness

    monkeypatch.setitem(CONFIG, "FOREX_H4_RESAMPLE_OFFSET_HOURS", 1.0)
    signal = {
        "type": "forex",
        "candleConsistency": {"H4": {"status": "CONFIRMED_ONLY_OK"}},
        "candleFetchMeta": {"H4": {"stalenessSeverity": "stale_1_bucket"}},
    }
    result = evaluate_execution_data_freshness(signal, CONFIG)
    assert result["allowed"] is True
    assert result["blocked"] == []
