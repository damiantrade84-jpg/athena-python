"""Diagnostic Engine B trigger-TF override (H1/M15/M30) — fail-closed extras."""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import config
from athena_app.services.market_state import _timeframe_seconds, get_bucket_start_epoch
from market_structure import (
    NakedEngine,
    engine_b_candles_for_tf,
    engine_b_diagnostic_trigger_kwargs,
    engine_b_live_trigger_kwargs,
    resolve_live_engine_b_trigger_tf,
)


def _series(close: float, n: int = 30, *, half_range: float = 0.5) -> list[dict]:
    return [
        {
            "open": close,
            "high": close + half_range,
            "low": close - half_range,
            "close": close,
            "vol": 100.0,
            "volume": 100.0,
        }
        for _ in range(n)
    ]


def test_m30_bucket_seconds_are_1800():
    assert _timeframe_seconds("M30") == 1800
    assert _timeframe_seconds("M15") == 900
    t0 = get_bucket_start_epoch("M30", 1_700_000_000.0)
    t1 = get_bucket_start_epoch("M30", 1_700_000_000.0 + 1800)
    assert t1 - t0 == 1800


def test_engine_b_candles_for_tf_extra_map_m15_no_h1_fallback():
    d1, h4, h1 = _series(1), _series(2), _series(3)
    m15 = _series(15.0, n=5)
    assert engine_b_candles_for_tf("M15", d1, h4, h1, extra_by_tf={"M15": m15})[-1][
        "close"
    ] == pytest.approx(15.0)
    # Missing key with extras provided → fail closed empty (not H1).
    assert engine_b_candles_for_tf("M15", d1, h4, h1, extra_by_tf={}) == []
    # Empty series → fail closed empty.
    assert engine_b_candles_for_tf("M15", d1, h4, h1, extra_by_tf={"M15": []}) == []
    # Legacy: no extras map → unknown TF falls back to H1.
    assert engine_b_candles_for_tf("M15", d1, h4, h1)[-1]["close"] == pytest.approx(3.0)


def test_diagnostic_trigger_kwargs_default_off(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_DIAGNOSTIC_TRIGGER_TF_ENABLED", False)
    assert engine_b_diagnostic_trigger_kwargs({"entry_tf": "M15"}, {"M15": _series(1)}) == {}


def test_diagnostic_trigger_kwargs_enabled(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_DIAGNOSTIC_TRIGGER_TF_ENABLED", True)
    m15 = _series(15.0)
    kw = engine_b_diagnostic_trigger_kwargs({"entry_tf": "M15"}, {"M15": m15})
    assert kw["trigger_tf_override"] == "M15"
    assert kw["role_candles"]["M15"][-1]["close"] == pytest.approx(15.0)
    assert engine_b_diagnostic_trigger_kwargs({"entry_tf": "H4"}, {"H4": m15}) == {}


def test_live_forex_intraday_trigger_is_m30_only_on_mt5():
    assert resolve_live_engine_b_trigger_tf(
        "forex", "intraday", source="mt5"
    ) == "M30"
    assert resolve_live_engine_b_trigger_tf(
        "forex", "intraday", source="eodhd"
    ) is None
    assert resolve_live_engine_b_trigger_tf("forex", "swing", source="mt5") is None


def test_live_trigger_kwargs_do_not_depend_on_diagnostic_flag(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_DIAGNOSTIC_TRIGGER_TF_ENABLED", False)
    m15 = _series(15.0)
    kw = engine_b_live_trigger_kwargs({"entry_tf": "M15"}, {"M15": m15})
    assert kw["trigger_tf_override"] == "M15"
    assert kw["role_candles"]["M15"] is m15


def test_live_lower_trigger_freshness_cannot_be_disabled(monkeypatch):
    import athena_app.services.market_state as market_state
    from scanner import _engine_b_scan_freshness_stale_tfs

    monkeypatch.setitem(config.CONFIG, "ENGINE_B_SCAN_FRESHNESS_GATE", False)
    monkeypatch.setattr(
        market_state,
        "candle_freshness_diagnostic",
        lambda _pair, tf, _candles, source=None: {
            "stalenessSeverity": "stale_1_bucket" if tf == "M15" else "fresh"
        },
    )
    stale, diag = _engine_b_scan_freshness_stale_tfs(
        {"display": "EUR/USD", "type": "forex", "source": "mt5"},
        _series(1.0),
        _series(1.0),
        _series(1.0),
        active_entry_tfs={"M15": _series(1.0)},
    )
    assert stale == ["M15:stale_1_bucket"]
    assert diag["M15"]["stalenessSeverity"] == "stale_1_bucket"


def test_intraday_override_m15_uses_m15_series_not_h1(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_STRIP_FORMING_STRUCT", False)
    d1 = _series(100.0)
    h4 = _series(150.0, half_range=2.0)
    h1 = _series(200.0)
    m15 = _series(15.0, half_range=0.1)
    pair = {"display": "EUR/USD", "type": "forex", "source": "mt5"}
    engine = NakedEngine()

    baseline = engine.precompute_structure_data(
        d1, h4, h1, 200.0, 1.0, style="intraday", asset_type="forex", pair=pair
    )
    assert baseline.get("_error") is None
    assert baseline["_tfs"]["trigger"] == "H1"
    assert baseline["_trigger_candles"][-1]["close"] == pytest.approx(200.0)

    overridden = engine.precompute_structure_data(
        d1,
        h4,
        h1,
        200.0,
        1.0,
        style="intraday",
        asset_type="forex",
        pair=pair,
        role_candles={"M15": m15, "M30": _series(30.0)},
        trigger_tf_override="M15",
    )
    assert overridden.get("_error") is None
    assert overridden["_tfs"]["trigger"] == "M15"
    assert overridden["_trigger_tf_override"] == "M15"
    assert overridden["_trigger_candles"][-1]["close"] == pytest.approx(15.0)
    # Structure still H4 for forex intraday.
    assert overridden["_structure_tf"] == "H4"
    assert overridden["trigger_atr"] == pytest.approx(0.2)
    assert overridden["struct_atr"] == pytest.approx(4.0)


def test_override_empty_m15_does_not_silently_use_h1(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_STRIP_FORMING_STRUCT", False)
    d1 = _series(100.0)
    h4 = _series(150.0)
    h1 = _series(200.0)
    pair = {"display": "EUR/USD", "type": "forex", "source": "mt5"}
    pre = NakedEngine().precompute_structure_data(
        d1,
        h4,
        h1,
        200.0,
        1.0,
        style="intraday",
        asset_type="forex",
        pair=pair,
        role_candles={"M15": []},
        trigger_tf_override="M15",
    )
    assert pre.get("_error") is None
    assert pre["_tfs"]["trigger"] == "M15"
    assert pre["_trigger_candles"] == []


def test_analyze_structure_stamps_override_trigger_timeframe(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_STRIP_FORMING_STRUCT", False)
    d1 = _series(100.0)
    h4 = _series(150.0)
    h1 = _series(200.0)
    m30 = _series(30.0)
    pair = {"display": "EUR/USD", "type": "forex", "source": "mt5"}
    res = NakedEngine().analyze_structure(
        d1,
        h4,
        h1,
        150.0,
        "LONG",
        1.0,
        style="intraday",
        asset_type="forex",
        pair=pair,
        role_candles={"M30": m30},
        trigger_tf_override="M30",
    )
    assert res.get("trigger_timeframe") == "M30"


def test_live_lower_trigger_gate_rejects_mismatched_trigger_timeframe(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_FOLLOW_THROUGH", {"ENABLED": False})
    base = {
        "atr": 1.0,
        "asset_type": "forex",
        "current_swing_sequence": "HH_HL",
        "macro_swing_sequence": "HH_HL",
        "bos_confirmed": True,
        "bos_mtf_confirmed": True,
        "liquidity_sweep": False,
        "zone_touched": True,
        "near_active_zone": False,
        "trigger_ok": True,
        "bos_volume_confirmed": True,
        "strong_close": True,
        "inside_break_candle": False,
        "engulfing_candle": False,
        "ob_at_zone": False,
        "breaker_block": False,
        "distance_to_res": 5.0,
        "distance_to_sup": 5.0,
        "recommended_stop_loss": 99.0,
        "recommended_take_profit": 105.0,
    }
    profile = {
        "style": "intraday",
        "entry_tf": "M15",
        "min_room_atr": 0.35,
        "min_rr": 1.0,
        "fallback_rr": 2.0,
        "require_macro_align": False,
    }

    blocked = NakedEngine().calculate_confidence(
        {**base, "trigger_timeframe": "H1"},
        current_price=100.0,
        direction="LONG",
        style_profile=profile,
    )
    assert blocked["trigger_timeframe_gate_required"] is True
    assert blocked["trigger_timeframe_gate_ok"] is False
    assert blocked["entry_ok"] is False
    assert "engine_b_trigger_timeframe_false" in blocked["hard_fail_reasons"]

    aligned = NakedEngine().calculate_confidence(
        {**base, "trigger_timeframe": "M15"},
        current_price=100.0,
        direction="LONG",
        style_profile=profile,
    )
    assert aligned["trigger_timeframe_gate_ok"] is True
    assert aligned["entry_ok"] is True
