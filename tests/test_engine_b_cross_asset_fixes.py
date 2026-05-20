import os
import sys
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import backtest_runner
import config
import execution
import scanner
from execution import (
    _apply_engine_b_execution_levels,
    _engine_b_atr_for_scan_levels,
    _engine_b_context_confirmed,
    _extract_engine_b_execution_levels,
    _refresh_engine_b_execution_context,
    _is_structural_engine_b_execution,
    _signal_has_engine_b_context,
)
from market_structure import NakedEngine, resolve_engine_b_asset_class
from scoring import get_pair_score_group


def test_scanner_demotes_trade_when_engine_b_not_aligned(monkeypatch):
    monkeypatch.setitem(scanner.CONFIG, "ENGINE_B_SCAN_CONFIRMATION_GATE_ENABLED", True)

    tier, reason = scanner._apply_engine_b_scan_gate(
        {"enginesAligned": False, "engine_b_verdict": "UNCLEAR"},
        "trade",
        "Trade-ready",
    )

    assert tier == "watchlist"
    assert "Engine B confirmation failed" in reason


def test_scanner_keeps_trade_when_gate_disabled_despite_engine_b_not_aligned(monkeypatch):
    monkeypatch.setitem(scanner.CONFIG, "ENGINE_B_SCAN_CONFIRMATION_GATE_ENABLED", False)

    tier, reason = scanner._apply_engine_b_scan_gate(
        {"enginesAligned": False, "engine_b_verdict": "UNCLEAR"},
        "trade",
        "Trade-ready",
    )

    assert tier == "trade"
    assert reason == "Trade-ready"


def test_scanner_keeps_trade_when_engine_b_aligned(monkeypatch):
    monkeypatch.setitem(scanner.CONFIG, "ENGINE_B_SCAN_CONFIRMATION_GATE_ENABLED", True)

    tier, reason = scanner._apply_engine_b_scan_gate(
        {"enginesAligned": True},
        "trade",
        "Trade-ready",
    )

    assert tier == "trade"
    assert reason == "Trade-ready"


def test_scanner_b_only_watchlist_is_default_off(monkeypatch):
    monkeypatch.setitem(scanner.CONFIG, "ENGINE_B_SCAN_B_ONLY_WATCHLIST_ENABLED", False)
    sig = {"engine_b_confidence_passed": True, "direction": "LONG", "engine_b_direction": "LONG"}

    tier, reason = scanner._apply_engine_b_only_watchlist_scan_tier(
        sig,
        "skip",
        "Below discovery threshold",
    )

    assert tier == "skip"
    assert reason == "Below discovery threshold"
    assert "engine_b_execution_blocked" not in sig


def test_scanner_b_only_watchlist_surfaces_passed_b_without_trade(monkeypatch):
    monkeypatch.setitem(scanner.CONFIG, "ENGINE_B_SCAN_B_ONLY_WATCHLIST_ENABLED", True)
    sig = {"engine_b_confidence_passed": True, "direction": "LONG", "engine_b_direction": "SHORT"}

    tier, reason = scanner._apply_engine_b_only_watchlist_scan_tier(
        sig,
        "skip",
        "Below discovery threshold",
    )

    assert tier == "watchlist"
    assert reason == "Engine B-only watchlist: B SHORT, A LONG"
    assert sig["engine_b_execution_blocked"] is True
    assert sig["engine_b_execution_block_reason"] == "engine_b_only_scan_watchlist"
    assert sig["scanDiagnostics"][-1]["code"] == "engine_b_only_watchlist"


def test_scanner_b_only_watchlist_does_not_override_a_scan_floor_watchlist(monkeypatch):
    monkeypatch.setitem(scanner.CONFIG, "ENGINE_B_SCAN_B_ONLY_WATCHLIST_ENABLED", True)
    sig = {
        "engine_b_confidence_passed": True,
        "direction": "SHORT",
        "engine_b_direction": "LONG",
        "confluenceScore": 2.38,
        "scanThreshold": 2.1,
    }

    tier, reason = scanner._apply_engine_b_only_watchlist_scan_tier(
        sig,
        "watchlist",
        "A-only auto gate requires about 2.50/3.0",
    )

    assert tier == "watchlist"
    assert reason == "A-only auto gate requires about 2.50/3.0"
    assert "engine_b_execution_block_reason" not in sig


def test_scanner_b_only_watchlist_does_not_override_safety_blocks(monkeypatch):
    monkeypatch.setitem(scanner.CONFIG, "ENGINE_B_SCAN_B_ONLY_WATCHLIST_ENABLED", True)
    sig = {
        "engine_b_confidence_passed": True,
        "direction": "LONG",
        "engine_b_direction": "LONG",
        "scanDiagnostics": [{"code": "closed_exchange", "detail": "Exchange closed"}],
    }

    tier, reason = scanner._apply_engine_b_only_watchlist_scan_tier(
        sig,
        "skip",
        "Exchange closed",
    )

    assert tier == "skip"
    assert reason == "Exchange closed"
    assert "engine_b_execution_blocked" not in sig


def test_scanner_applies_engine_b_execution_levels_to_signal(monkeypatch):
    monkeypatch.setitem(scanner.CONFIG, "ENGINE_B_USE_EXECUTION_LEVELS_FOR_SCAN_SIGNALS", True)
    # Engine identity must be explicitly Engine B for generic SL/TP to be
    # overwritten — Engine A rows are protected regardless of this flag.
    signal = {
        "engine": "B",
        "engine_source": "ENGINE_B",
        "sl": 90.0,
        "tp1": 110.0,
        "tp2": 120.0,
    }

    scanner._apply_engine_b_scan_levels(
        signal,
        {"execution_sl": 98.5, "execution_tp": 103.0, "rr_used_for_gate": 2.0},
        {"recommended_stop_loss": 95.0, "recommended_take_profit": 105.0},
    )

    assert signal["sl"] == pytest.approx(98.5)
    assert signal["tp1"] == pytest.approx(103.0)
    assert signal["tp2"] == pytest.approx(103.0)
    assert signal["levelSource"] == "engine_b_execution"
    assert signal["engine_b_rr_used_for_gate"] == pytest.approx(2.0)


def test_scanner_engine_b_alignment_uses_final_score_gate():
    signal = {}
    conf_b = {"passed": True, "score": 3.0}

    gate_ok, scaled_min = scanner._apply_engine_b_scan_confidence_gate(
        signal,
        conf_b,
        {"min_score": 5.0},
        "RANGING",
        "forex",
    )

    assert scaled_min == pytest.approx(4.5)
    assert gate_ok is False
    assert signal["enginesAligned"] is False
    assert signal["engine_b_min_score_scaled"] == pytest.approx(4.5)
    assert conf_b["passed"] is False
    assert conf_b["min_score_scaled"] == pytest.approx(4.5)


def test_scanner_engine_b_score_floor_accepts_mandatory_gate_baseline():
    signal = {}
    conf_b = {"passed": True, "score": 5.0}

    gate_ok, scaled_min = scanner._apply_engine_b_scan_confidence_gate(
        signal,
        conf_b,
        {"min_score": 5.0},
        "RANGING",
        "forex",
    )

    assert scaled_min == pytest.approx(4.5)
    assert gate_ok is True
    assert signal["enginesAligned"] is True
    assert signal["engine_b_min_score_scaled"] == pytest.approx(4.5)
    assert conf_b["passed"] is True
    assert conf_b["min_score_scaled"] == pytest.approx(4.5)


def test_checked_in_engine_b_thresholds_match_reachable_gate_baseline():
    profiles = config.CONFIG["NAKED_ENGINE"]["style_profiles"]
    overrides = config.CONFIG["NAKED_ENGINE"]["score_group_overrides"]

    assert profiles["scalp"]["min_score"] == pytest.approx(4.0)
    assert profiles["intraday"]["min_score"] == pytest.approx(4.0)
    assert profiles["swing"]["min_score"] == pytest.approx(4.0)
    assert overrides["nat_gas"]["scalp"]["min_score"] == pytest.approx(4.0)
    assert overrides["nat_gas"]["intraday"]["min_score"] == pytest.approx(4.0)
    assert overrides["nat_gas"]["swing"]["min_score"] == pytest.approx(4.0)
    assert overrides["crypto_doge"]["scalp"]["min_score"] == pytest.approx(4.0)
    assert overrides["crypto_doge"]["intraday"]["min_score"] == pytest.approx(4.0)
    assert overrides["crypto_doge"]["swing"]["min_score"] == pytest.approx(4.0)


def test_scanner_opposite_independent_engine_b_does_not_raise_conviction():
    combined = scanner._engine_b_scan_combined_conviction(
        a_norm=0.50,
        b_norm=1.00,
        weights={"A": 0.40, "B": 0.60},
        direction_aligned=False,
    )

    assert combined == pytest.approx(0.30)


def test_scanner_aligned_engine_b_uses_regime_weights_for_conviction():
    combined = scanner._engine_b_scan_combined_conviction(
        a_norm=0.50,
        b_norm=1.00,
        weights={"A": 0.40, "B": 0.60},
        direction_aligned=True,
    )

    assert combined == pytest.approx(0.80)


def test_execution_prefers_nested_engine_b_execution_levels():
    sig = {
        "is_naked": True,
        "sl": 90.0,
        "tp1": 110.0,
        "naked_data": {"execution_sl": 98.5, "execution_tp": 103.0},
    }

    assert _extract_engine_b_execution_levels(sig) == {
        "sl": 98.5,
        "tp1": 103.0,
        "tp2": 103.0,
    }
    assert _apply_engine_b_execution_levels(sig) is True
    assert sig["sl"] == pytest.approx(98.5)
    assert sig["tp1"] == pytest.approx(103.0)
    assert sig["level_source"] == "engine_b_execution"


def test_execution_prefers_top_level_resolved_engine_b_levels_over_raw_overlay():
    sig = {
        "engine": "B",
        "sl": 1.0900,
        "tp1": 1.1300,
        "engine_b_execution_sl": 1.0950,
        "engine_b_execution_tp": 1.1250,
        "engine_b": {
            "recommended_stop_loss": 1.0800,
            "recommended_take_profit": 1.1400,
            "passed": True,
        },
    }

    assert _extract_engine_b_execution_levels(sig) == {
        "sl": 1.0950,
        "tp1": 1.1250,
        "tp2": 1.1250,
    }


def test_execution_engine_a_scan_engines_aligned_is_not_structural_b_context():
    """A+B merge sets enginesAligned on Engine A rows; execution must not treat that as B-only."""
    sig_false = {"enginesAligned": False, "sl": 98.0, "tp1": 103.0}
    assert _is_structural_engine_b_execution(sig_false) is False
    assert _signal_has_engine_b_context(sig_false) is False
    assert _engine_b_context_confirmed(sig_false) is True

    sig_true = {"enginesAligned": True, "sl": 98.0, "tp1": 103.0}
    assert _is_structural_engine_b_execution(sig_true) is False
    assert _engine_b_context_confirmed(sig_true) is True


def test_execution_engine_a_raw_engine_b_overlay_is_not_structural_b_context():
    sig = {
        "engine": "A",
        "enginesAligned": False,
        "sl": 1.0900,
        "tp1": 1.1300,
        "engine_b_execution_sl": 1.0950,
        "engine_b_execution_tp": 1.1250,
        "engine_b": {
            "structural_verdict": "CLEAR",
            "recommended_stop_loss": 1.0800,
            "recommended_take_profit": 1.1400,
            "passed": False,
        },
    }

    assert _is_structural_engine_b_execution(sig) is False
    assert _signal_has_engine_b_context(sig) is False
    assert _engine_b_context_confirmed(sig) is True


def test_execution_structural_b_still_honors_engines_aligned_when_key_present():
    sig = {
        "is_naked": True,
        "enginesAligned": False,
        "naked_data": {"passed": True, "execution_sl": 1.0, "execution_tp": 2.0},
    }
    # enginesAligned checked before nested passed — unchanged ordering for structural B
    assert _engine_b_context_confirmed(sig, {}) is False

    sig_ok = {**sig, "enginesAligned": True}
    assert _engine_b_context_confirmed(sig_ok, {}) is True
    assert (
        _engine_b_context_confirmed(
            {"is_naked": True, "naked_data": {"execution_sl": 1.0, "execution_tp": 2.0}},
            {},
        )
        is False
    )
    assert (
        _engine_b_context_confirmed(
            {
                "is_naked": True,
                "naked_data": {"passed": True, "execution_sl": 1.0, "execution_tp": 2.0},
            },
            {},
        )
        is True
    )


def test_execution_accepts_pass_in_top_level_engine_b_payload():
    assert _engine_b_context_confirmed({"is_naked": True}, {"passed": True}) is True


def test_engine_b_execution_refresh_requests_fail_closed_execution_mode():
    calls = {}

    def refresh(seed, **kwargs):
        calls.update(kwargs)
        return (
            {
                "passed": True,
                "direction": "LONG",
                "current_price": 100.0,
                "execution_sl": 98.0,
                "execution_tp": 104.0,
            },
            {"display": "TEST"},
            None,
        )

    sig = {"pair": "TEST", "direction": "LONG", "is_naked": True}
    refreshed, err = _refresh_engine_b_execution_context(
        sig,
        {},
        SimpleNamespace(compute_naked_analysis=refresh),
        "intraday",
    )

    assert err is None
    assert refreshed is not None
    assert calls["force_ai"] is False
    assert calls["execution_mode"] is True


def test_choch_uses_bos_reference_level_when_bos_context_present():
    engine = NakedEngine()
    highs = np.array([100.0, 102.0, 104.0, 103.0])
    lows = np.array([99.0, 100.0, 101.0, 98.0])
    closes = np.array([100.0, 101.5, 103.5, 98.5])
    swings = {"peak_idx": [0, 1, 2], "trough_idx": [0, 1, 2]}

    out = engine._detect_choch(
        highs,
        lows,
        atr=1.0,
        closes=closes,
        swings=swings,
        bos_data={"bos_bull": True, "bos_bear": False, "bos_reference_low": 100.0},
    )

    assert out["choch_bear"] is True
    assert out["choch_level"] == pytest.approx(100.0)
    assert out["choch_events"] == [{"type": "bearish", "level": 100.0}]


def test_engine_b_backtest_mt5_does_not_call_eodhd_or_yfinance_fallback(monkeypatch):
    calls = {"fetch_candles": [], "fetch_eodhd": 0, "fetch_yfinance": 0}

    def _fetch_candles(pair, tf, limit):
        calls["fetch_candles"].append((pair["source"], tf, limit))
        return []

    def _unexpected_eodhd(*_args, **_kwargs):
        calls["fetch_eodhd"] += 1
        raise AssertionError("Engine B MT5 backtest must not call EODHD price fallback")

    def _unexpected_yfinance(*_args, **_kwargs):
        calls["fetch_yfinance"] += 1
        raise AssertionError("Engine B MT5 backtest must not call yfinance price fallback")

    monkeypatch.setattr(
        backtest_runner,
        "_rt",
        lambda: SimpleNamespace(
            fetch_candles=_fetch_candles,
            fetch_eodhd=_unexpected_eodhd,
            fetch_yfinance=_unexpected_yfinance,
            extract_candles=lambda raw: raw,
            yfinance_symbol_for_pair=lambda _pair: "EURUSD=X",
        ),
    )
    monkeypatch.setattr(
        backtest_runner,
        "fetch_backtest_candles",
        lambda pair, tf, limit, fetcher, **_kwargs: fetcher(limit),
    )

    result = backtest_runner.backtest_pair_naked(
        {"display": "EUR/USD", "symbol": "EURUSD", "type": "forex", "source": "mt5"},
        style="intraday",
    )

    assert result["success"] is False
    assert calls["fetch_eodhd"] == 0
    assert calls["fetch_yfinance"] == 0
    assert [tf for _, tf, _ in calls["fetch_candles"]] == ["D1", "H4", "H1"]


def test_engine_b_backtest_non_mt5_uses_source_router_not_eodhd_primary(monkeypatch):
    calls = {"fetch_candles": [], "fetch_eodhd": 0}

    def _fetch_candles(pair, tf, limit):
        calls["fetch_candles"].append((pair["source"], tf, limit))
        return []

    def _unexpected_eodhd(*_args, **_kwargs):
        calls["fetch_eodhd"] += 1
        raise AssertionError("Engine B source-routed backtest must not force EODHD")

    monkeypatch.setattr(
        backtest_runner,
        "_rt",
        lambda: SimpleNamespace(
            fetch_candles=_fetch_candles,
            fetch_eodhd=_unexpected_eodhd,
            extract_candles=lambda raw: raw,
        ),
    )
    monkeypatch.setattr(
        backtest_runner,
        "fetch_backtest_candles",
        lambda pair, tf, limit, fetcher, **_kwargs: fetcher(limit),
    )

    result = backtest_runner.backtest_pair_naked(
        {"display": "TEST", "symbol": "TEST", "type": "stock", "source": "polygon"},
        style="intraday",
    )

    assert result["success"] is False
    assert calls["fetch_eodhd"] == 0
    assert [tf for _, tf, _ in calls["fetch_candles"]] == ["D1", "H4", "H1"]


def test_engine_c_live_atr_ignores_engine_a_h4_atr_for_d1_style(monkeypatch):
    monkeypatch.setattr(execution, "calc_atr", lambda *_args, **_kwargs: [4.25])

    atr, source = _engine_b_atr_for_scan_levels(
        {"atr": 1.0},
        [{"high": 110.0, "low": 100.0, "close": 105.0}],
        "D1",
        {"display": "EUR/USD", "type": "forex"},
        "swing",
        SimpleNamespace(CONFIG={"ENGINE_B_CRYPTO_LEVELS_FEED": "signal"}),
    )

    assert atr == pytest.approx(4.25)
    assert source == "D1"


def test_engine_c_backtest_atr_uses_matching_timeframe_index():
    atr_map = {
        "D1": [10.0, 11.0],
        "H4": [100.0 + i for i in range(12)],
        "H1": [200.0 + i for i in range(60)],
    }

    d1_atr, _, d1_idx = backtest_runner._engine_b_indexed_atr(
        atr_map,
        "D1",
        d1_idx=2,
        h4_idx=12,
        h1_idx=48,
        current_price=1000.0,
    )
    h1_atr, _, h1_idx = backtest_runner._engine_b_indexed_atr(
        atr_map,
        "H1",
        d1_idx=2,
        h4_idx=12,
        h1_idx=48,
        current_price=1000.0,
    )

    assert d1_idx == 2
    assert d1_atr == pytest.approx(11.0)
    assert h1_idx == 48
    assert h1_atr == pytest.approx(247.0)


def test_crypto_bos_volume_average_excludes_zero_volume(monkeypatch):
    monkeypatch.setitem(config.CONFIG["NAKED_ENGINE"], "bos_volume_multiplier", 1.3)
    engine = NakedEngine()
    highs = np.array([100.0 + i for i in range(25)])
    lows = highs - 2.0
    closes = highs - 0.1
    volumes = np.array([100.0] * 5 + [0.0] * 10 + [100.0] * 9 + [125.0])
    swings = {
        "peak_idx": np.array([5, 10, 15]),
        "trough_idx": np.array([4, 9, 14]),
    }

    out = engine._detect_bos(
        highs,
        lows,
        atr=1.0,
        volumes=volumes,
        closes=closes,
        swings=swings,
    )

    assert out["bos_bull"] is True
    assert out["bos_volume_confirmed"] is False


def test_crypto_min_room_atr_is_style_aware():
    engine = NakedEngine()
    base_res = {
        "atr": 10.0,
        "asset_type": "crypto",
        "current_swing_sequence": "HH_HL",
        "macro_swing_sequence": "HH_HL",
        "bos_confirmed": True,
        "near_active_zone": True,
        "trigger_ok": True,
        "recommended_stop_loss": 90.0,
        "recommended_take_profit": 120.0,
        "distance_to_res": 3.0,
    }

    swing_conf = engine.calculate_confidence(
        dict(base_res),
        100.0,
        "LONG",
        style_profile={"style": "swing", "min_rr": 1.0, "fallback_rr": 2.0},
    )
    intraday_conf = engine.calculate_confidence(
        dict(base_res),
        100.0,
        "LONG",
        style_profile={"style": "intraday", "min_rr": 1.0, "fallback_rr": 2.0},
    )

    assert swing_conf["min_room_atr_used"] == pytest.approx(0.40)
    assert swing_conf["room_ok"] is False
    assert intraday_conf["min_room_atr_used"] == pytest.approx(0.25)
    assert intraday_conf["room_ok"] is True


def test_stock_typed_commodity_tracker_score_group_forces_swing_macro_alignment():
    engine = NakedEngine()
    res = {
        "atr": 10.0,
        "asset_type": "stock",
        "current_swing_sequence": "HH_HL",
        "macro_swing_sequence": "LH_LL",
        "bos_confirmed": False,
        "near_active_zone": True,
        "trigger_ok": True,
        "recommended_stop_loss": 90.0,
        "recommended_take_profit": 120.0,
        "distance_to_res": 10.0,
    }

    conf = engine.calculate_confidence(
        res,
        100.0,
        "LONG",
        style_profile={
            "style": "swing",
            "score_group": "precious_trackers",
            "require_macro_align": False,
            "min_rr": 1.0,
            "fallback_rr": 2.0,
        },
    )

    assert conf["macro_ok"] is False
    assert conf["passed"] is False


def test_index_and_precious_etf_score_groups_route_to_etf_engine_b_class():
    assert get_pair_score_group({"display": "DIA", "type": "stock"}) == "us_indices_trackers"
    assert get_pair_score_group({"display": "GDX", "type": "stock"}) == "precious_trackers"
    assert resolve_engine_b_asset_class("stock", "us_indices_trackers") == "etf"
    assert resolve_engine_b_asset_class("stock", "precious_trackers") == "etf"
