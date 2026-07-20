from datetime import datetime, timezone
from pathlib import Path

from athena_app.services.engine_b_market_state import engine_b_live_market_state
from athena_app.services.market_state import (
    get_bucket_start_epoch,
    get_tf_market_state,
    market_state_offset_hours,
    split_market_state,
)
from config import CONFIG


def _epoch(iso_ts: str) -> int:
    return int(datetime.fromisoformat(iso_ts.replace("Z", "+00:00")).timestamp())


def _candle(iso_ts: str) -> dict:
    return {
        "time": iso_ts,
        "open": 1.0,
        "high": 1.1,
        "low": 0.9,
        "close": 1.05,
        "vol": 100.0,
    }


def test_h4_offset_zero_uses_standard_utc_buckets():
    assert datetime.fromtimestamp(
        get_bucket_start_epoch("H4", _epoch("2026-04-24T00:30:00Z"), offset_hours=0),
        tz=timezone.utc,
    ).hour == 0
    assert datetime.fromtimestamp(
        get_bucket_start_epoch("H4", _epoch("2026-04-24T04:30:00Z"), offset_hours=0),
        tz=timezone.utc,
    ).hour == 4
    assert datetime.fromtimestamp(
        get_bucket_start_epoch("H4", _epoch("2026-04-24T08:30:00Z"), offset_hours=0),
        tz=timezone.utc,
    ).hour == 8


def test_h4_offset_one_uses_forex_shifted_buckets():
    assert datetime.fromtimestamp(
        get_bucket_start_epoch("H4", _epoch("2026-04-24T01:30:00Z"), offset_hours=1),
        tz=timezone.utc,
    ).hour == 1
    assert datetime.fromtimestamp(
        get_bucket_start_epoch("H4", _epoch("2026-04-24T05:30:00Z"), offset_hours=1),
        tz=timezone.utc,
    ).hour == 5
    assert datetime.fromtimestamp(
        get_bucket_start_epoch("H4", _epoch("2026-04-24T09:30:00Z"), offset_hours=1),
        tz=timezone.utc,
    ).hour == 9


def test_offset_h4_active_bucket_is_forming_and_previous_is_confirmed():
    candles = [
        _candle("2026-04-24T05:00:00Z"),
        _candle("2026-04-24T09:00:00Z"),
    ]

    state = split_market_state(
        candles,
        "H4",
        "EUR/USD",
        time_now=_epoch("2026-04-24T09:30:00Z"),
        offset_hours=1,
    )

    assert [c["time"] for c in state["confirmed"]] == ["2026-04-24T05:00:00Z"]
    assert state["forming"]["time"] == "2026-04-24T09:00:00Z"
    assert state["is_live"] is True


def test_mt5_forex_scan_helpers_use_offset_aware_confirmed_h4(monkeypatch):
    monkeypatch.setitem(CONFIG, "FOREX_H4_RESAMPLE_OFFSET_HOURS", 1.0)
    pair = {
        "display": "EUR/USD",
        "symbol": "EURUSD",
        "type": "forex",
        "source": "mt5",
    }
    candles = [
        _candle("2026-04-24T05:00:00Z"),
        _candle("2026-04-24T09:00:00Z"),
    ]
    now = _epoch("2026-04-24T09:30:00Z")

    engine_a_state = get_tf_market_state(pair, "H4", candles=candles, time_now=now)
    engine_b_state = engine_b_live_market_state(
        pair,
        "H4",
        len(candles),
        candles=candles,
        time_now=now,
    )

    assert [c["time"] for c in engine_a_state["confirmed"]] == ["2026-04-24T05:00:00Z"]
    assert engine_a_state["forming"]["time"] == "2026-04-24T09:00:00Z"
    assert [c["time"] for c in engine_b_state["confirmed"]] == ["2026-04-24T05:00:00Z"]
    assert engine_b_state["forming"]["time"] == "2026-04-24T09:00:00Z"


def test_mt5_stock_h4_uses_three_hour_confirmed_split():
    pair = {
        "display": "AAPL",
        "symbol": "AAPL",
        "type": "stock",
        "source": "mt5",
    }
    candles = [
        _candle("2026-04-24T15:00:00Z"),
        _candle("2026-04-24T19:00:00Z"),
    ]

    state = get_tf_market_state(
        pair,
        "H4",
        candles=candles,
        time_now=_epoch("2026-04-24T19:15:00Z"),
    )

    assert market_state_offset_hours(pair, "H4") == 3.0
    assert [c["time"] for c in state["confirmed"]] == ["2026-04-24T15:00:00Z"]
    assert state["forming"]["time"] == "2026-04-24T19:00:00Z"


def test_mt5_commodity_and_index_h4_use_broker_offset(monkeypatch):
    monkeypatch.setitem(CONFIG, "FOREX_H4_RESAMPLE_OFFSET_HOURS", 2.0)
    for asset_type, symbol in (("commodity", "XAU/USD"), ("index", "NAS100")):
        pair = {
            "display": symbol,
            "symbol": symbol.replace("/", ""),
            "type": asset_type,
            "source": "mt5",
        }
        candles = [
            _candle("2026-04-24T06:00:00Z"),
            _candle("2026-04-24T10:00:00Z"),
        ]

        state = get_tf_market_state(
            pair,
            "H4",
            candles=candles,
            time_now=_epoch("2026-04-24T10:30:00Z"),
        )

        assert market_state_offset_hours(pair, "H4") == 2.0
        assert [c["time"] for c in state["confirmed"]] == ["2026-04-24T06:00:00Z"]
        assert state["forming"]["time"] == "2026-04-24T10:00:00Z"


def test_mt5_forex_d1_offset_defaults_zero_without_override(monkeypatch):
    pair = {
        "display": "EUR/USD",
        "symbol": "EURUSD",
        "type": "forex",
        "source": "mt5",
    }
    monkeypatch.delitem(CONFIG, "D1_RESAMPLE_OFFSET_HOURS", raising=False)
    se = dict(CONFIG.get("SCALP_ENGINE") or {})
    se.pop("D1_RESAMPLE_OFFSET_HOURS", None)
    monkeypatch.setitem(CONFIG, "SCALP_ENGINE", se)

    assert market_state_offset_hours(pair, "D1") == 0.0


def test_d1_offset_reads_scalp_engine_nesting(monkeypatch):
    pair = {"display": "EUR/USD", "source": "mt5", "type": "forex"}
    monkeypatch.delitem(CONFIG, "D1_RESAMPLE_OFFSET_HOURS", raising=False)
    monkeypatch.setitem(CONFIG, "SCALP_ENGINE", {"D1_RESAMPLE_OFFSET_HOURS": 2.5})
    assert market_state_offset_hours(pair, "D1") == 2.5


def test_d1_offset_top_level_wins_over_scalp_nested(monkeypatch):
    pair = {"display": "EUR/USD", "source": "mt5", "type": "forex"}
    monkeypatch.setitem(CONFIG, "D1_RESAMPLE_OFFSET_HOURS", 0.75)
    monkeypatch.setitem(
        CONFIG, "SCALP_ENGINE", {"D1_RESAMPLE_OFFSET_HOURS": 9.99, "FOO": "bar"}
    )
    assert market_state_offset_hours(pair, "D1") == 0.75


def test_d1_offset_split_marks_session_tail_forming(monkeypatch):
    monkeypatch.setitem(CONFIG, "D1_RESAMPLE_OFFSET_HOURS", 2.0)
    pair = {"display": "XAU/USD", "source": "mt5", "type": "commodity"}
    candles = [
        _candle("2026-05-06T02:00:00Z"),
        _candle("2026-05-07T02:00:00Z"),
    ]

    state = get_tf_market_state(
        pair,
        "D1",
        candles=candles,
        time_now=_epoch("2026-05-07T12:00:00Z"),
    )

    assert [c["time"] for c in state["confirmed"]] == ["2026-05-06T02:00:00Z"]
    assert state["forming"]["time"] == "2026-05-07T02:00:00Z"


def test_engine_b_mt5_d1_trims_broker_session_ahead_tail():
    pair = {
        "display": "EUR/USD",
        "symbol": "EURUSD",
        "type": "forex",
        "source": "mt5",
    }
    candles = [
        _candle("2026-05-06T00:00:00Z"),
        _candle("2026-05-07T00:00:00Z"),
        _candle("2026-05-08T00:00:00Z"),
    ]

    state = engine_b_live_market_state(
        pair,
        "D1",
        len(candles),
        candles=candles,
        time_now=_epoch("2026-05-07T12:00:00Z"),
    )

    assert [c["time"] for c in state["confirmed"]] == ["2026-05-06T00:00:00Z"]
    assert state["forming"]["time"] == "2026-05-07T00:00:00Z"


def test_engine_b_d1_weekend_gap_does_not_mark_stale(monkeypatch):
    """Fri→Mon D1 lag must not set stale=True on Engine B live state.

    Regression: engine_b_live_market_state returned raw split_market_state, whose
    age>tf_seconds stale flag ignored D1 calendar-gap grace and blocked execute
    with ENGINE_B_ENTRY_NOT_READY: UNAVAILABLE: stale_required_closed_timeframes:D1.
    """
    monkeypatch.setitem(CONFIG, "MT5_D1_CALENDAR_GAP_GRACE_BUCKETS", 4)
    pair = {
        "display": "EUR/USD",
        "symbol": "EURUSD",
        "type": "forex",
        "source": "mt5",
    }
    # Friday open; evaluate Monday noon (bucket lag 3 within grace=4).
    candles = [_candle("2026-05-08T00:00:00Z")] * 40
    now = _epoch("2026-05-11T12:00:00Z")

    crude = split_market_state(
        candles,
        "D1",
        pair["display"],
        time_now=now,
        offset_hours=market_state_offset_hours(pair, "D1"),
    )
    assert crude["stale"] is True

    state = engine_b_live_market_state(
        pair,
        "D1",
        len(candles),
        candles=candles,
        time_now=now,
    )
    assert state["stale"] is False
    assert state.get("confirmed")


def test_scanner_preloads_engine_a_market_state_for_all_mt5_assets():
    src = Path("scanner.py").read_text(encoding="utf-8")
    assert 'if pair.get("source") == "mt5":' in src
    assert 'pair.get("type") == "forex"' not in src[src.index('preloaded_market_state = {}'):src.index('fetch_meta = {')]


def test_analyze_pair_fallback_split_uses_pair_timeframe_offset():
    src = Path("athena.py").read_text(encoding="utf-8")
    assert "offset_hours=market_state_offset_hours(pair, tf)" in src
    assert "confirmedSplitFailed" in src
