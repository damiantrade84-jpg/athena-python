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
    # No extras map → fail closed; never substitute H1.
    assert engine_b_candles_for_tf("M15", d1, h4, h1) == []


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


def test_legacy_live_trigger_override_is_not_resurrected_by_defaults():
    assert "LIVE_TRIGGER_TF_BY_STYLE" not in config.CONFIG.get("NAKED_ENGINE", {})


def test_live_trigger_kwargs_do_not_depend_on_diagnostic_flag(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_DIAGNOSTIC_TRIGGER_TF_ENABLED", False)
    m15 = _series(15.0)
    kw = engine_b_live_trigger_kwargs({"entry_tf": "M15"}, {"M15": m15})
    assert kw["trigger_tf_override"] == "M15"
    assert kw["role_candles"]["M15"] is m15


def test_scanner_provenance_does_not_relabel_computed_timeframes():
    from types import SimpleNamespace

    from scanner import _attach_engine_b_timeframe_provenance

    def tf(value):
        return SimpleNamespace(value=value)

    policy = SimpleNamespace(
        structure_tf=tf("H1"),
        setup_tf=tf("M30"),
        trigger_tf=tf("M15"),
        execution_tf=tf("M15"),
    )
    result = {"structure_tf": "H4", "trigger_timeframe": "H1"}

    _attach_engine_b_timeframe_provenance(
        result,
        policy,
        actual_structure_tf="H4",
        actual_trigger_tf="H1",
        actual_atr_tf="H4",
    )

    assert result["structure_tf"] == "H4"
    assert result["entry_tf"] == "H1"
    assert result["trigger_tf"] == "H1"
    assert result["execution_tf"] == "M15"
    assert result["execution_tf_actual"] is None
    assert result["execution_tf_consumed"] is False
    assert result["atr_tf"] == "H4"
    assert result["structure_tf_policy"] == "H1"
    assert result["trigger_tf_policy"] == "M15"
    assert result["atr_tf_policy"] == "H1"


# test_engine_b_backtest_requests_policy_atr_timeframe removed with the legacy
# backtester (_engine_b_level_atr_for_bt lives in archive/backtest_legacy/);
# the v3 rebuild derives ATR from its own per-TF series cache at the policy's
# atr_tf (athena_backtest/engines/engine_b.py).


def test_live_lower_trigger_freshness_honors_disabled_gate(monkeypatch):
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
    assert stale == []
    assert diag == {}


def test_intraday_override_m15_uses_m15_series_not_h1(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_STRIP_FORMING_STRUCT", False)
    d1 = _series(100.0)
    h4 = _series(150.0, half_range=2.0)
    h1 = _series(200.0)
    m15 = _series(15.0, half_range=0.1)
    pair = {"display": "EUR/USD", "type": "forex", "source": "mt5"}
    engine = NakedEngine()

    # Enforced policy makes M15 the required intraday trigger series; calling
    # without it fails closed instead of silently substituting H1.
    baseline = engine.precompute_structure_data(
        d1, h4, h1, 200.0, 1.0, style="intraday", asset_type="forex", pair=pair
    )
    assert baseline.get("_error") == "missing_required_trigger_timeframe:M15"

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
    # Structure follows the authoritative policy (intraday H1); trigger noise
    # never resizes structural ATR.
    assert overridden["_structure_tf"] == "H1"
    assert overridden["trigger_atr"] == pytest.approx(0.2)
    assert overridden["struct_atr"] == pytest.approx(1.0)


def test_h4_macro_sequence_uses_h4_atr_not_policy_zone_atr(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "TF_POLICY_MODE", "enforced_demo")
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_STRIP_FORMING_STRUCT", False)
    d1 = _series(100.0, half_range=6.0)
    h4 = _series(100.0, half_range=4.0)
    h1 = _series(100.0, half_range=0.5)
    m15 = _series(100.0, half_range=0.1)

    engine = NakedEngine()
    swing_atrs = []
    original_swing_cache = engine._swing_cache

    def _capture_swing_atr(highs, lows, atr, *args, **kwargs):
        swing_atrs.append(atr)
        return original_swing_cache(highs, lows, atr, *args, **kwargs)

    monkeypatch.setattr(engine, "_swing_cache", _capture_swing_atr)
    pre = engine.precompute_structure_data(
        d1,
        h4,
        h1,
        100.0,
        1.0,
        style="intraday",
        asset_type="forex",
        pair={"display": "EUR/USD", "type": "forex", "source": "mt5"},
        role_candles={"M15": m15},
        trigger_tf_override="M15",
    )

    assert pre.get("_error") is None
    assert pre["_tfs"]["zone"] == "H1"
    assert pre["zone_atr"] == pytest.approx(1.0)
    assert pre["h4_atr"] == pytest.approx(8.0)
    assert swing_atrs[1] == pytest.approx(pre["h4_atr"])


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
    # An empty override series fails closed — never silently uses H1.
    assert pre.get("_error") == "missing_required_trigger_timeframe:M15"


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
