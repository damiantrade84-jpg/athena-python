"""Regression checks for backtest realism fixes."""

import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import backtest_runner
from intermarket import (
    build_point_in_time_context,
    discover_active_universe,
    prepare_series_store,
)


def _make_bars(start_dt, count, hours, base=100.0):
    bars = []
    for i in range(count):
        price = base + (i * 0.01)
        bars.append(
            {
                "time": (start_dt + timedelta(hours=hours * i)).isoformat(),
                "open": price,
                "high": price + 0.5,
                "low": price - 0.5,
                "close": price,
                "vol": 1000.0,
            }
        )
    return bars


def test_bt_inject_vol_skew_enriches_context_when_ortho_enabled(monkeypatch):
    calls = []

    def _fake_get_vol_skew_z(display, as_of_date=None):
        calls.append((display, as_of_date))
        return 0.75

    monkeypatch.setitem(backtest_runner.CONFIG, "ENGINE_A_ORTHO_VOTE_ENABLED", True)
    monkeypatch.setitem(
        sys.modules,
        "vol_skew_feed",
        SimpleNamespace(get_vol_skew_z=_fake_get_vol_skew_z),
    )

    ctx = backtest_runner._bt_inject_vol_skew(
        {"usd_relative_strength": {"state": "risk_on"}},
        {"display": "XAU/USD"},
        "2025-06-01 04:00:00+00:00",
    )

    assert calls == [("XAU/USD", "2025-06-01")]
    assert ctx["usd_relative_strength"] == {"state": "risk_on"}
    assert ctx["vol_skew"] == pytest.approx(0.75)


def test_bt_inject_vol_skew_skips_when_ortho_disabled(monkeypatch):
    def _raise_if_called(_display, as_of_date=None):
        raise AssertionError("vol_skew feed should not run when ortho is disabled")

    monkeypatch.setitem(backtest_runner.CONFIG, "ENGINE_A_ORTHO_VOTE_ENABLED", False)
    monkeypatch.setitem(
        sys.modules,
        "vol_skew_feed",
        SimpleNamespace(get_vol_skew_z=_raise_if_called),
    )

    ctx = {"usd_relative_strength": {"state": "risk_on"}}

    assert backtest_runner._bt_inject_vol_skew(ctx, {"display": "XAU/USD"}, "2025-06-01") is ctx


def _source_block(src: str, start_marker: str, end_marker: str) -> str:
    start = src.index(start_marker)
    end = src.index(end_marker, start)
    return src[start:end]


def test_volatility_slippage_model_never_understates_legacy(monkeypatch):
    bar = {"time": "2024-01-01T12:00:00+00:00", "open": 100.0, "close": 100.0, "atr": 5.0}
    monkeypatch.setitem(
        backtest_runner.CONFIG,
        "BT_SLIPPAGE_MODEL",
        {
            "ENABLED": True,
            "K1_TICK_MULT": 1.0,
            "K2_ATR_IMPACT": 0.5,
            "DEFAULT_QTY_ADV_RATIO": 0.04,
            "MAX_SLIPPAGE_PCT": 0.10,
        },
    )

    legacy = backtest_runner._BASE_BACKTEST_SLIP["stock"]
    modeled = backtest_runner._get_slippage_for_bar(bar, "stock")

    assert modeled > legacy


def test_volatility_slippage_model_can_be_disabled(monkeypatch):
    bar = {"time": "2024-01-01T12:00:00+00:00", "open": 100.0, "close": 100.0, "atr": 5.0}
    monkeypatch.setitem(backtest_runner.CONFIG, "BT_SLIPPAGE_MODEL", {"ENABLED": False})

    assert backtest_runner._get_slippage_for_bar(bar, "stock") == backtest_runner._BASE_BACKTEST_SLIP["stock"]


def _aligned_multi_tf_candles(start_dt, *, h1_count=400):
    """Build time-aligned D1/H4/H1 series for a run_v3_backtest walk.

    H1 is the intraday primary; D1/H4 are the higher timeframes whose still-
    forming bar must never reach a decision.
    """
    h1 = _make_bars(start_dt, h1_count, 1, base=100.0)
    h4 = _make_bars(start_dt, h1_count // 4 + 2, 4, base=100.0)
    d1 = _make_bars(start_dt, h1_count // 24 + 3, 24, base=100.0)
    return {"D1": d1, "H4": h4, "H1": h1}


def test_engine_a_v3_backtest_excludes_unclosed_higher_tf_bars(monkeypatch):
    """Behavioral anti-lookahead guard for the ACTIVE Engine A V3 backtester.

    Every prefix handed to the evaluator must contain, for each higher timeframe,
    only bars that have fully CLOSED by the primary decision bar's close. A same-
    day D1 (or same-4h H4) bar that is still forming at the decision would leak
    its eventual close into scoring and inflate results vs live.
    """
    from engine_a_v3 import backtest as v3bt
    from athena_app.services.market_state import candle_timestamp_epoch

    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    candles = _aligned_multi_tf_candles(start)

    captured: list[dict] = []

    def _recording_eval(pair, prefix, **kwargs):
        captured.append({tf: list(rows) for tf, rows in prefix.items()})
        return SimpleNamespace(decision="WATCH", qualified=False)

    monkeypatch.setattr(v3bt, "evaluate_engine_a_v3", _recording_eval)

    v3bt.run_v3_backtest(
        {"display": "TEST/USD", "symbol": "TESTUSD", "type": "forex"},
        candles,
        horizon="intraday",
        spread_bps=0.0,
        commission_bps=0.0,
        slippage_bps=0.0,
        swap_bps_per_day=0.0,
    )

    assert captured, "run_v3_backtest never evaluated a bar"
    tf_seconds = {"H1": 3600, "H4": 14400, "D1": 86400}
    for prefix in captured:
        primary = prefix["H1"]
        assert primary, "empty primary prefix"
        decision_close = candle_timestamp_epoch(primary[-1]) + tf_seconds["H1"]
        for tf in ("D1", "H4"):
            for bar in prefix[tf]:
                bar_close = candle_timestamp_epoch(bar) + tf_seconds[tf]
                assert bar_close <= decision_close, (
                    f"{tf} bar closing at {bar_close} leaked into a decision "
                    f"closing at {decision_close}"
                )


def test_engine_a_v3_confirmed_cutoff_excludes_same_day_d1():
    """Unit check of the anti-lookahead cutoff rule used by _build_prefix_at."""
    from engine_a_v3.backtest import confirmed_cutoff_open_epoch

    # H4 decision bar opening 2024-01-05 08:00 UTC (closes 12:00).
    primary_open = int(datetime(2024, 1, 5, 8, tzinfo=timezone.utc).timestamp())
    cutoff = confirmed_cutoff_open_epoch("H4", primary_open, "D1")

    same_day_d1 = int(datetime(2024, 1, 5, 0, tzinfo=timezone.utc).timestamp())
    prev_day_d1 = int(datetime(2024, 1, 4, 0, tzinfo=timezone.utc).timestamp())
    # Same-day D1 (closes 2024-01-06 00:00) is still forming → excluded.
    assert same_day_d1 > cutoff
    # Previous-day D1 (closed 2024-01-05 00:00) is confirmed → included.
    assert prev_day_d1 <= cutoff


def test_engine_b_backtest_validates_post_fill_level_sides_before_rr():
    src = Path(backtest_runner.__file__).read_text(encoding="utf-8")
    assert "invalid post-fill levels" in src
    side_check = 'direction == "LONG" and sl < entry < tp'
    rr_check = 'if target_rr < float(style_profile.get("min_rr", 1.0)):'
    assert side_check in src
    assert src.index(side_check) < src.index(rr_check)


def test_resolve_barrier_exit_prefers_sl_when_long_bar_hits_tp_and_sl():
    outcome, both_hit = backtest_runner._resolve_barrier_exit(
        {"high": 105.0, "low": 94.0},
        direction="LONG",
        sl=95.0,
        tp1=103.0,
        tp2=104.0,
    )
    assert both_hit is True
    assert outcome == "SL"


def test_resolve_barrier_exit_prefers_sl_when_short_bar_hits_tp_and_sl():
    outcome, both_hit = backtest_runner._resolve_barrier_exit(
        {"high": 106.0, "low": 96.0},
        direction="SHORT",
        sl=105.0,
        tp1=98.0,
        tp2=97.0,
    )
    assert both_hit is True
    assert outcome == "SL"


def test_engine_a_trade_path_diagnostics_marks_loser_that_reached_half_r():
    diagnostics = backtest_runner._engine_a_trade_path_diagnostics(
        [
            {"high": 100.7, "low": 99.8},
            {"high": 100.4, "low": 98.9},
        ],
        direction="LONG",
        entry=100.0,
        sl=99.0,
        tp1=102.0,
        tp2=103.0,
    )

    assert diagnostics["max_favorable_excursion_r"] == 0.7
    assert diagnostics["max_adverse_excursion_r"] == -1.1
    assert diagnostics["reached_half_r"] is True
    assert diagnostics["reached_one_r"] is False
    assert diagnostics["reached_sl"] is True
    assert diagnostics["reached_tp1"] is False


def test_engine_a_exit_diagnostics_summary_counts_sl_path_quality():
    summary = backtest_runner._engine_a_exit_diagnostics_summary(
        [
            {
                "outcome": "SL",
                "resultR": -1.0,
                "reached_half_r": True,
                "reached_one_r": False,
                "max_favorable_excursion_r": 0.7,
                "max_adverse_excursion_r": -1.1,
            },
            {
                "outcome": "TP1",
                "resultR": 2.0,
                "reached_half_r": True,
                "reached_one_r": True,
                "max_favorable_excursion_r": 2.1,
                "max_adverse_excursion_r": -0.2,
            },
        ],
        same_bar_both_hit=3,
    )

    assert summary["outcomes"] == {"SL": 1, "TP1": 1}
    assert summary["sameBarBothHit"] == 3
    assert summary["lossesReachedHalfR"] == 100.0
    assert summary["slReachedOneR"] == 0.0


def test_engine_b_confidence_gate_requires_passed_and_score_floor():
    from market_structure import engine_b_confidence_passes

    style_profile = {"min_score": 1.1}

    gate_ok, scaled_min = engine_b_confidence_passes(
        {"score": 1.0, "passed": False},
        style_profile,
        "TRENDING",
    )
    assert round(scaled_min, 3) == 1.0
    assert gate_ok is False

    gate_ok, scaled_min = engine_b_confidence_passes(
        {"score": 2.0, "passed": True},
        style_profile,
        "TRENDING",
    )
    assert round(scaled_min, 3) == 1.0
    assert gate_ok is True

    gate_ok, scaled_min = engine_b_confidence_passes(
        {"score": 2.6, "passed": True},
        {"min_score": 3.0},
        "HIGH_VOLATILITY",
    )
    assert round(scaled_min, 3) == 2.6
    assert gate_ok is True


def test_backtest_pair_naked_enters_on_next_bar_open_with_slippage(monkeypatch):
    pair = {"display": "AAPL", "symbol": "AAPL", "type": "stock", "source": "eodhd"}
    d1 = _make_bars(datetime(2024, 1, 1, tzinfo=timezone.utc), 100, 24, base=90.0)
    h4 = _make_bars(datetime(2024, 2, 1, tzinfo=timezone.utc), 70, 4, base=99.0)
    h1 = _make_bars(datetime(2024, 2, 1, tzinfo=timezone.utc), 400, 1, base=100.0)

    h4[50]["close"] = 100.0
    h4[51]["open"] = 101.0
    h4[51]["high"] = 103.5
    h4[51]["low"] = 100.6
    h4[51]["close"] = 103.0

    audit_dir = Path(os.path.dirname(__file__)) / "_artifacts"
    audit_dir.mkdir(exist_ok=True)
    runtime = SimpleNamespace(
        fetch_eodhd=lambda *_args, **_kwargs: d1,
        extract_candles=lambda candles: candles,
        fetch_candles=lambda *_args, **_kwargs: d1,
        fetch_eodhd_intraday_bt=lambda *_args, **_kwargs: (h4, h1),
        naked_scan_style_profile=lambda style, score_group=None, asset_type=None, **_kwargs: (
            "intraday",
            {
                "min_score": 0.5,
                "fallback_rr": 2.0,
                "min_rr": 1.0,
                "atr_tf": "H4",
                "entry_tf": "H4",
                "zone_tf": "H4",
            },
        ),
        engine_b_regime_label=lambda *_args, **_kwargs: "TRENDING",
        AUDIT_DB=str(audit_dir / "audit.db"),
    )

    slip_calls = []

    def _track_slip(bar, ptype, **_kwargs):
        slip_calls.append((float(bar.get("open", bar.get("close", 0))), ptype))
        return 0.001

    monkeypatch.setattr(backtest_runner, "_rt", lambda: runtime)
    monkeypatch.setattr(backtest_runner, "_get_slippage_for_bar", _track_slip)
    monkeypatch.setattr(backtest_runner, "get_pair_score_group", lambda _pair: "default")
    monkeypatch.setattr(
        backtest_runner,
        "calc_levels",
        lambda entry, atr, direction, ptype, regime_state=None, style="intraday": {
            "sl": entry - 1.0 if direction == "LONG" else entry + 1.0,
            "tp1": entry + 2.0 if direction == "LONG" else entry - 2.0,
            "tp2": entry + 3.0 if direction == "LONG" else entry - 3.0,
            "rr1": 2.0,
            "rr2": 3.0,
        },
    )
    monkeypatch.setattr(backtest_runner, "enrich_backtest_summary", lambda result, returns: result)
    monkeypatch.setattr(backtest_runner, "record_backtest_summary", lambda *args, **kwargs: None)
    monkeypatch.setattr(backtest_runner, "calibration_report", lambda *args, **kwargs: {})
    monkeypatch.setattr(backtest_runner, "meta_report", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        backtest_runner,
        "_format_backtest_results",
        lambda trades, pair, engine_type="NAKED", same_bar_both_hit=0, **kwargs: {
            "pair": pair["display"],
            "engine": engine_type,
            "totalTrades": len(trades),
            "trades": trades,
            "same_bar_both_hit": same_bar_both_hit,
            "winRate": 100.0 if trades else 0.0,
            "profitFactor": 1.0,
            "expectancy": 0.0,
            "sqn": 0.0,
        },
    )

    import market_structure

    monkeypatch.setattr(
        market_structure.engine,
        "analyze_structure_direction",
        lambda *_args, **_kwargs: {
            "structural_verdict": "CLEAR",
            "order_blocks": [],
            "liquidity_sweep": False,
            "fvg_overlap": False,
            "bos_volume_confirmed": True,
            "choch_confirmed": False,
            "ob_at_zone": False,
            "bos_mtf_confirmed": False,
            "fvg_bonus": 0.0,
            "volume_strength": 0.0,
        },
    )
    geometry_prices = []

    def _confidence_at_acting_price(_res, _px, direction, **_kwargs):
        geometry_prices.append(float(_px))
        return {
            "score": 1.0 if direction == "LONG" else 0.0,
            "pct": 80.0 if direction == "LONG" else 0.0,
            "rr": 2.0,
            "passed": direction == "LONG",
            "trigger_pattern": "NONE",
            "max_possible": 5.0,
            "structure_ok": direction == "LONG",
            "macro_ok": True,
            "zone_ok": True,
            "breakout_ok": False,
            "location_ok": direction == "LONG",
            "trigger_ok": direction == "LONG",
            "entry_ok": direction == "LONG",
            "room_ok": direction == "LONG",
            "rr_ok": direction == "LONG",
            "tp_side_ok": direction == "LONG",
            "space_ok": direction == "LONG",
            "execution_sl": _px - 1.0 if direction == "LONG" else _px + 1.0,
            "execution_tp": _px + 2.0 if direction == "LONG" else _px - 2.0,
            "rr_source": "test_fixture",
        }

    monkeypatch.setattr(
        market_structure.engine,
        "calculate_confidence",
        _confidence_at_acting_price,
    )

    result = backtest_runner.backtest_pair_naked(pair, style="intraday")

    assert result["totalTrades"] >= 1
    assert "researchValidation" in result
    assert result["researchValidation"].get("validationMode") == "standard"
    assert slip_calls, "slippage helper should run for each fill"
    assert geometry_prices
    acting_opens = {
        float(bar["open"])
        for bar in (d1 + h4 + h1)
    }
    assert all(price in acting_opens for price in geometry_prices)
    # Next-bar open with slippage: LONG entry = open + open*slip (slip fixed at 0.001 here).
    long_trades = [t for t in result["trades"] if t.get("direction") == "LONG"]
    assert long_trades
    for t in long_trades[:3]:
        # Patched slip is fractional (0.001), applied as raw_entry * 0.001
        assert t["entry"] > 0
    assert result["same_bar_both_hit"] == 0


def test_backtest_pair_naked_skips_profile_context_when_profile_scoring_disabled(monkeypatch):
    pair = {"display": "AAPL", "symbol": "AAPL", "type": "stock", "source": "eodhd"}
    d1 = _make_bars(datetime(2024, 1, 1, tzinfo=timezone.utc), 100, 24, base=90.0)
    h4 = _make_bars(datetime(2024, 2, 1, tzinfo=timezone.utc), 70, 4, base=99.0)
    h1 = _make_bars(datetime(2024, 2, 1, tzinfo=timezone.utc), 400, 1, base=100.0)

    audit_dir = Path(os.path.dirname(__file__)) / "_artifacts"
    audit_dir.mkdir(exist_ok=True)
    runtime = SimpleNamespace(
        fetch_eodhd=lambda *_args, **_kwargs: d1,
        extract_candles=lambda candles: candles,
        fetch_candles=lambda *_args, **_kwargs: d1,
        fetch_eodhd_intraday_bt=lambda *_args, **_kwargs: (h4, h1),
        naked_scan_style_profile=lambda style, score_group=None, asset_type=None: (
            "intraday",
            {"min_score": 0.5, "fallback_rr": 2.0, "min_rr": 1.0, "atr_tf": "H4"},
        ),
        engine_b_regime_label=lambda *_args, **_kwargs: "TRENDING",
        AUDIT_DB=str(audit_dir / "audit.db"),
    )

    captured = []
    pre_captured = []

    monkeypatch.setattr(backtest_runner, "_rt", lambda: runtime)
    monkeypatch.setattr(backtest_runner, "get_pair_score_group", lambda _pair: "default")
    monkeypatch.setattr(backtest_runner, "enrich_backtest_summary", lambda result, returns: result)
    monkeypatch.setattr(backtest_runner, "record_backtest_summary", lambda *args, **kwargs: None)
    monkeypatch.setattr(backtest_runner, "calibration_report", lambda *args, **kwargs: {})
    monkeypatch.setattr(backtest_runner, "meta_report", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        backtest_runner,
        "_format_backtest_results",
        lambda trades, pair, engine_type="NAKED", same_bar_both_hit=0, **kwargs: {
            "pair": pair["display"],
            "engine": engine_type,
            "totalTrades": len(trades),
            "trades": trades,
            "same_bar_both_hit": same_bar_both_hit,
            "winRate": 0.0,
            "profitFactor": 0.0,
            "expectancy": 0.0,
            "sqn": 0.0,
        },
    )
    monkeypatch.setitem(backtest_runner.CONFIG, "ENGINE_B_PROFILE_SCORING_ENABLED", False)

    import market_structure

    def _capture_pre(*args, **kwargs):
        pre_captured.append(kwargs)
        return {"_error": None}

    def _capture_analyze_dir(*_args, **kwargs):
        captured.append(kwargs)
        return {
            "structural_verdict": "CLEAR",
            "order_blocks": [],
            "liquidity_sweep": False,
            "fvg_overlap": False,
            "bos_volume_confirmed": True,
            "choch_confirmed": False,
            "ob_at_zone": False,
            "bos_mtf_confirmed": False,
            "fvg_bonus": 0.0,
            "volume_strength": 0.0,
        }

    monkeypatch.setattr(market_structure.engine, "precompute_structure_data", _capture_pre)
    monkeypatch.setattr(market_structure.engine, "analyze_structure_direction", _capture_analyze_dir)
    monkeypatch.setattr(
        market_structure.engine,
        "calculate_confidence",
        lambda _res, _px, direction, **_kwargs: {
            "score": 1.0 if direction == "LONG" else 0.0,
            "pct": 80.0 if direction == "LONG" else 0.0,
            "rr": 2.0,
            "passed": direction == "LONG",
            "trigger_pattern": "NONE",
            "max_possible": 5.0,
        },
    )

    backtest_runner.backtest_pair_naked(pair, style="intraday")

    assert captured
    assert all(call.get("enable_profile_context") is False for call in pre_captured)



def test_backtest_pair_naked_forex_auto_keeps_intraday_style_under_d1_structure(monkeypatch):
    pair = {"display": "USD/CHF", "symbol": "USDCHF", "type": "forex", "source": "mt5"}
    d1 = _make_bars(datetime(2024, 1, 1, tzinfo=timezone.utc), 260, 24, base=0.90)
    h4 = _make_bars(datetime(2024, 2, 1, tzinfo=timezone.utc), 620, 4, base=0.89)
    h1 = _make_bars(datetime(2024, 2, 1, tzinfo=timezone.utc), 1100, 1, base=0.88)

    audit_dir = Path(os.path.dirname(__file__)) / "_artifacts"
    audit_dir.mkdir(exist_ok=True)

    style_calls = []
    runtime = SimpleNamespace(
        fetch_candles=lambda _pair, tf, _limit: {"D1": d1, "H4": h4, "H1": h1}[tf],
        naked_scan_style_profile=lambda style, score_group=None, asset_type=None: (
            style_calls.append((style, score_group)) or (
                "intraday",
                {
                    "min_score": 0.5,
                    "fallback_rr": 2.0,
                    "min_rr": 1.0,
                    "zone_tf": "H4",
                    "entry_tf": "H1",
                    "atr_tf": "H4",
                },
            )
        ),
        engine_b_regime_label=lambda *_args, **_kwargs: "TRENDING",
        AUDIT_DB=str(audit_dir / "audit.db"),
    )

    monkeypatch.setattr(backtest_runner, "_rt", lambda: runtime)
    monkeypatch.setattr(backtest_runner, "_get_slippage_for_bar", lambda *_args, **_kwargs: 0.0)
    monkeypatch.setattr(backtest_runner, "get_pair_score_group", lambda _pair: "forex_majors")
    monkeypatch.setattr(backtest_runner, "record_backtest_summary", lambda *args, **kwargs: None)
    monkeypatch.setattr(backtest_runner, "calibration_report", lambda *args, **kwargs: {})
    monkeypatch.setattr(backtest_runner, "meta_report", lambda *args, **kwargs: {})
    monkeypatch.setattr(backtest_runner, "enrich_backtest_summary", lambda result, returns, **kwargs: result)
    monkeypatch.setattr(
        backtest_runner,
        "_format_backtest_results",
        lambda trades, pair, engine_type="NAKED", same_bar_both_hit=0, **kwargs: {
            "pair": pair["display"],
            "engine": engine_type,
            "totalTrades": len(trades),
            "trades": trades,
            "same_bar_both_hit": same_bar_both_hit,
            "winRate": 0.0,
            "profitFactor": 0.0,
            "expectancy": 0.0,
            "sqn": 0.0,
            "maxDrawdownPct": 0.0,
            "sharpe": 0.0,
            "sortino": 0.0,
            "wfSplit": {},
        },
    )

    import market_structure

    monkeypatch.setattr(
        market_structure.engine,
        "analyze_structure_direction",
        lambda *_args, **_kwargs: {
            "structural_verdict": "CLEAR",
            "recommended_stop_loss": 0.8790,
            "recommended_take_profit": 0.8840,
            "order_blocks": [],
            "liquidity_sweep": False,
            "fvg_overlap": False,
            "bos_volume_confirmed": True,
            "choch_confirmed": False,
            "ob_at_zone": False,
            "bos_mtf_confirmed": False,
            "d1_pd_array_conflict": False,
        },
    )
    monkeypatch.setattr(
        market_structure.engine,
        "calculate_confidence",
        lambda _res, _px, direction, **_kwargs: {
            "score": 1.0 if direction == "LONG" else 0.0,
            "pct": 80.0 if direction == "LONG" else 0.0,
            "rr": 2.0,
            "passed": direction == "LONG",
            "structure_ok": direction == "LONG",
            "location_ok": direction == "LONG",
            "entry_ok": direction == "LONG",
            "rr_ok": direction == "LONG",
            "room_ok": direction == "LONG",
            "macro_ok": True,
            "max_possible": 5.0,
        },
    )
    monkeypatch.setitem(backtest_runner.CONFIG, "ENGINE_B_FOREX_STRUCTURE_TF", "D1")
    monkeypatch.setitem(backtest_runner.CONFIG, "ENGINE_B_PROFILE_SCORING_ENABLED", False)

    result = backtest_runner.backtest_pair_naked(pair, style="naked")

    assert result["btStyle"] == "intraday"
    assert style_calls == [("auto", "forex_majors")]


def test_backtest_pair_naked_caps_post_fill_rr_to_style_fallback(monkeypatch):
    pair = {"display": "AAPL", "symbol": "AAPL", "type": "stock", "source": "eodhd"}
    d1 = _make_bars(datetime(2024, 1, 1, tzinfo=timezone.utc), 100, 24, base=90.0)
    h4 = _make_bars(datetime(2024, 2, 1, tzinfo=timezone.utc), 70, 4, base=99.0)
    h1 = _make_bars(datetime(2024, 2, 1, tzinfo=timezone.utc), 400, 1, base=100.0)

    audit_dir = Path(os.path.dirname(__file__)) / "_artifacts"
    audit_dir.mkdir(exist_ok=True)
    runtime = SimpleNamespace(
        fetch_eodhd=lambda *_args, **_kwargs: d1,
        extract_candles=lambda candles: candles,
        fetch_candles=lambda *_args, **_kwargs: d1,
        fetch_eodhd_intraday_bt=lambda *_args, **_kwargs: (h4, h1),
        naked_scan_style_profile=lambda style, score_group=None, asset_type=None: (
            "intraday",
            {"min_score": 0.5, "fallback_rr": 2.0, "min_rr": 1.0, "atr_tf": "H4"},
        ),
        engine_b_regime_label=lambda *_args, **_kwargs: "TRENDING",
        AUDIT_DB=str(audit_dir / "audit.db"),
    )

    monkeypatch.setattr(backtest_runner, "_rt", lambda: runtime)
    monkeypatch.setattr(backtest_runner, "_get_slippage_for_bar", lambda *_args, **_kwargs: 0.0)
    monkeypatch.setattr(backtest_runner, "get_pair_score_group", lambda _pair: "default")
    monkeypatch.setattr(
        backtest_runner,
        "calc_levels",
        lambda entry, atr, direction, ptype, regime_state=None, style="intraday": {
            "sl": entry - 1.0 if direction == "LONG" else entry + 1.0,
            "tp1": entry + 2.0 if direction == "LONG" else entry - 2.0,
            "tp2": entry + 3.0 if direction == "LONG" else entry - 3.0,
            "rr1": 2.0,
            "rr2": 3.0,
        },
    )
    monkeypatch.setattr(backtest_runner, "enrich_backtest_summary", lambda result, returns: result)
    monkeypatch.setattr(backtest_runner, "record_backtest_summary", lambda *args, **kwargs: None)
    monkeypatch.setattr(backtest_runner, "calibration_report", lambda *args, **kwargs: {})
    monkeypatch.setattr(backtest_runner, "meta_report", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        backtest_runner,
        "_format_backtest_results",
        lambda trades, pair, engine_type="NAKED", same_bar_both_hit=0, **kwargs: {
            "pair": pair["display"],
            "engine": engine_type,
            "totalTrades": len(trades),
            "trades": trades,
            "same_bar_both_hit": same_bar_both_hit,
            "winRate": 0.0,
            "profitFactor": 0.0,
            "expectancy": 0.0,
            "sqn": 0.0,
        },
    )

    import market_structure

    monkeypatch.setattr(
        market_structure.engine,
        "analyze_structure_direction",
        lambda *_args, **_kwargs: {
            "structural_verdict": "CLEAR",
            "recommended_stop_loss": 95.0,
            "recommended_take_profit": 120.0,
            "regime_state": "TRENDING",
            "order_blocks": [],
            "liquidity_sweep": False,
            "fvg_overlap": False,
            "bos_volume_confirmed": True,
            "choch_confirmed": False,
            "ob_at_zone": False,
            "bos_mtf_confirmed": False,
            "fvg_bonus": 0.0,
            "volume_strength": 0.0,
        },
    )
    monkeypatch.setattr(
        market_structure.engine,
        "calculate_confidence",
        lambda _res, px, direction, **_kwargs: {
            "score": 2.0 if direction == "LONG" else 0.0,
            "pct": 80.0 if direction == "LONG" else 0.0,
            "rr": 2.4 if direction == "LONG" else 0.0,
            "passed": direction == "LONG",
            "trigger_pattern": "NONE",
            "max_possible": 5.0,
            "structure_ok": direction == "LONG",
            "macro_ok": True,
            "zone_ok": True,
            "breakout_ok": False,
            "location_ok": direction == "LONG",
            "trigger_ok": direction == "LONG",
            "entry_ok": direction == "LONG",
            "room_ok": direction == "LONG",
            "rr_ok": direction == "LONG",
            "tp_side_ok": direction == "LONG",
            "space_ok": direction == "LONG",
            # Engine B execution levels feed straight into BT now (live parity).
            # Wide structural targets get capped to fallback_rr at fill time.
            "execution_sl": px - 1.0 if direction == "LONG" else px + 1.0,
            "execution_tp": px + 4.0 if direction == "LONG" else px - 4.0,
            "rr_used_for_gate": 4.0 if direction == "LONG" else 0.0,
            "rr_source": "atr_sl_structural_tp",
        },
    )

    result = backtest_runner.backtest_pair_naked(pair, style="intraday")

    assert result["totalTrades"] >= 1
    first = result["trades"][0]
    assert first["rr_target"] == 2.0
    assert first["selected_tp_source"] == "capped_to_fallback_rr"


def test_backtest_pair_naked_preserves_engine_b_tp1_tp2_exit_plan(monkeypatch):
    pair = {"display": "AAPL", "symbol": "AAPL", "type": "stock", "source": "eodhd"}
    d1 = _make_bars(datetime(2024, 1, 1, tzinfo=timezone.utc), 100, 24, base=90.0)
    h4 = _make_bars(datetime(2024, 2, 1, tzinfo=timezone.utc), 70, 4, base=99.0)
    h1 = _make_bars(datetime(2024, 2, 1, tzinfo=timezone.utc), 400, 1, base=100.0)

    audit_dir = Path(os.path.dirname(__file__)) / "_artifacts"
    audit_dir.mkdir(exist_ok=True)
    runtime = SimpleNamespace(
        fetch_eodhd=lambda *_args, **_kwargs: d1,
        extract_candles=lambda candles: candles,
        fetch_candles=lambda *_args, **_kwargs: d1,
        fetch_eodhd_intraday_bt=lambda *_args, **_kwargs: (h4, h1),
        naked_scan_style_profile=lambda style, score_group=None, asset_type=None, symbol=None: (
            "intraday",
            {"min_score": 0.5, "fallback_rr": 2.0, "min_rr": 2.5, "atr_tf": "H4"},
        ),
        engine_b_regime_label=lambda *_args, **_kwargs: "TRENDING",
        AUDIT_DB=str(audit_dir / "audit.db"),
    )

    monkeypatch.setattr(backtest_runner, "_rt", lambda: runtime)
    monkeypatch.setattr(backtest_runner, "_get_slippage_for_bar", lambda *_args, **_kwargs: 0.0)
    monkeypatch.setattr(backtest_runner, "get_pair_score_group", lambda _pair: "default")
    monkeypatch.setattr(backtest_runner, "enrich_backtest_summary", lambda result, returns: result)
    monkeypatch.setattr(backtest_runner, "record_backtest_summary", lambda *args, **kwargs: None)
    monkeypatch.setattr(backtest_runner, "calibration_report", lambda *args, **kwargs: {})
    monkeypatch.setattr(backtest_runner, "meta_report", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        backtest_runner,
        "_format_backtest_results",
        lambda trades, pair, engine_type="NAKED", same_bar_both_hit=0, **kwargs: {
            "pair": pair["display"],
            "engine": engine_type,
            "totalTrades": len(trades),
            "trades": trades,
            "same_bar_both_hit": same_bar_both_hit,
            "winRate": 0.0,
            "profitFactor": 0.0,
            "expectancy": 0.0,
            "sqn": 0.0,
        },
    )

    import market_structure

    monkeypatch.setattr(
        market_structure.engine,
        "analyze_structure_direction",
        lambda *_args, **_kwargs: {
            "structural_verdict": "CLEAR",
            "recommended_stop_loss": 95.0,
            "recommended_take_profit": 101.25,
            "regime_state": "TRENDING",
            "order_blocks": [],
            "liquidity_sweep": False,
            "fvg_overlap": False,
            "bos_volume_confirmed": True,
            "choch_confirmed": False,
            "ob_at_zone": False,
            "bos_mtf_confirmed": False,
            "fvg_bonus": 0.0,
            "volume_strength": 0.0,
        },
    )

    def _confidence(_res, px, direction, **_kwargs):
        if direction != "LONG":
            return {
                "score": 0.0,
                "pct": 0.0,
                "passed": False,
                "max_possible": 5.0,
            }
        return {
            "score": 2.0,
            "pct": 80.0,
            "rr": 2.0,
            "passed": True,
            "trigger_pattern": "NONE",
            "max_possible": 5.0,
            "structure_ok": True,
            "macro_ok": True,
            "zone_ok": True,
            "breakout_ok": False,
            "location_ok": True,
            "trigger_ok": True,
            "entry_ok": True,
            "room_ok": True,
            "rr_ok": True,
            "tp_side_ok": True,
            "space_ok": True,
            "execution_sl": px - 1.0,
            "execution_tp": px + 2.0,
            "execution_tp1": px + 0.75,
            "execution_tp2": px + 2.0,
            "execution_rr1": 0.75,
            "execution_rr2": 2.0,
            "rr_used_for_gate": 2.0,
            # The resolver emitted its executable runner threshold. The BT
            # must not re-apply the higher style floor and reject this plan.
            "rr_required": 2.0,
            "rr_source": "atr_sl_fallback_rr_tp",
            "exit_strategy": "scale_out_structural_tp1_fallback_tp2",
        }

    monkeypatch.setattr(market_structure.engine, "calculate_confidence", _confidence)

    result = backtest_runner.backtest_pair_naked(pair, style="intraday")

    assert result["totalTrades"] >= 1
    first = result["trades"][0]
    assert first["tp2"] > first["tp1"]
    assert first["tp2"] - first["tp1"] == pytest.approx(1.25)
    assert first["rr1"] > 0.0
    assert first["rr1"] < first["rr2"]
    assert first["rr2"] == pytest.approx(first["rr_target"])
    assert first["exit_strategy"] == "scale_out_structural_tp1_fallback_tp2"


def test_backtest_pair_naked_scale_out_runner_can_reach_tp2(monkeypatch):
    pair = {"display": "AAPL", "symbol": "AAPL", "type": "stock", "source": "mt5"}
    d1 = _make_bars(datetime(2024, 1, 1, tzinfo=timezone.utc), 100, 24, base=90.0)
    h4 = _make_bars(datetime(2024, 2, 1, tzinfo=timezone.utc), 80, 4, base=99.0)
    h1 = _make_bars(datetime(2024, 2, 1, tzinfo=timezone.utc), 400, 1, base=100.0)
    h1[81].update({"high": 101.60, "low": 100.60, "close": 101.50})
    h1[82].update({"high": 102.90, "low": 101.00, "close": 102.70})

    audit_dir = Path(os.path.dirname(__file__)) / "_artifacts"
    audit_dir.mkdir(exist_ok=True)
    runtime = SimpleNamespace(
        fetch_candles=lambda _pair, tf, _limit: {"D1": d1, "H4": h4, "H1": h1}[tf],
        naked_scan_style_profile=lambda style, score_group=None, asset_type=None: (
            "intraday",
            {"min_score": 0.5, "fallback_rr": 2.0, "min_rr": 1.0, "atr_tf": "H4"},
        ),
        engine_b_regime_label=lambda *_args, **_kwargs: "TRENDING",
        AUDIT_DB=str(audit_dir / "audit.db"),
    )

    monkeypatch.setattr(backtest_runner, "_rt", lambda: runtime)
    monkeypatch.setattr(backtest_runner, "_get_slippage_for_bar", lambda *_args, **_kwargs: 0.0)
    monkeypatch.setattr(backtest_runner, "_bt_transaction_cost_r", lambda *_args, **_kwargs: 0.0)
    monkeypatch.setattr(backtest_runner, "get_pair_score_group", lambda _pair: "default")
    monkeypatch.setattr(backtest_runner, "enrich_backtest_summary", lambda result, returns: result)
    monkeypatch.setattr(backtest_runner, "record_backtest_summary", lambda *args, **kwargs: None)
    monkeypatch.setattr(backtest_runner, "calibration_report", lambda *args, **kwargs: {})
    monkeypatch.setattr(backtest_runner, "meta_report", lambda *args, **kwargs: {})
    monkeypatch.setitem(backtest_runner.CONFIG, "ENGINE_B_BT_SCALE_OUT_TP1_SIZE", 0.5)
    monkeypatch.setitem(backtest_runner.CONFIG, "ENGINE_B_BT_MOVE_SL_TO_BE_AFTER_TP1", True)
    monkeypatch.setitem(backtest_runner.CONFIG, "ENGINE_B_BT_RUNNER_ENABLED", True)
    monkeypatch.setattr(
        backtest_runner,
        "_format_backtest_results",
        lambda trades, pair, engine_type="NAKED", same_bar_both_hit=0, **kwargs: {
            "pair": pair["display"],
            "engine": engine_type,
            "totalTrades": len(trades),
            "trades": trades,
            "same_bar_both_hit": same_bar_both_hit,
            "winRate": 0.0,
            "profitFactor": 0.0,
            "expectancy": 0.0,
            "sqn": 0.0,
        },
    )

    import market_structure

    monkeypatch.setattr(
        market_structure.engine,
        "analyze_structure_direction",
        lambda *_args, **_kwargs: {
            "structural_verdict": "CLEAR",
            "recommended_stop_loss": 99.5,
            "recommended_take_profit": 101.25,
            "regime_state": "TRENDING",
            "order_blocks": [],
            "liquidity_sweep": False,
            "fvg_overlap": False,
            "bos_volume_confirmed": True,
            "choch_confirmed": False,
            "ob_at_zone": False,
            "bos_mtf_confirmed": False,
            "fvg_bonus": 0.0,
            "volume_strength": 0.0,
        },
    )

    def _confidence(_res, px, direction, **_kwargs):
        if direction != "LONG":
            return {"score": 0.0, "pct": 0.0, "passed": False, "max_possible": 5.0}
        return {
            "score": 2.0,
            "pct": 80.0,
            "rr": 2.0,
            "passed": True,
            "trigger_pattern": "NONE",
            "max_possible": 5.0,
            "structure_ok": True,
            "macro_ok": True,
            "zone_ok": True,
            "breakout_ok": False,
            "location_ok": True,
            "trigger_ok": True,
            "entry_ok": True,
            "room_ok": True,
            "rr_ok": True,
            "tp_side_ok": True,
            "space_ok": True,
            "execution_sl": px - 1.0,
            "execution_tp": px + 2.0,
            "execution_tp1": px + 0.75,
            "execution_tp2": px + 2.0,
            "execution_rr1": 0.75,
            "execution_rr2": 2.0,
            "rr_used_for_gate": 2.0,
            "rr_source": "atr_sl_fallback_rr_tp",
            "exit_strategy": "scale_out_structural_tp1_fallback_tp2",
        }

    monkeypatch.setattr(market_structure.engine, "calculate_confidence", _confidence)

    result = backtest_runner.backtest_pair_naked(pair, style="intraday")

    assert result["totalTrades"] >= 1
    first = result["trades"][0]
    assert first["outcome"] == "TP2"
    assert first["partial_taken_tp1"] is True
    assert first["runner_be_armed"] is True
    expected_r = round((first["rr1"] * 0.5) + (first["rr2"] * 0.5), 2)
    assert first["resultR"] == pytest.approx(expected_r)


def test_runner_requires_structural_break_blocks_tp2_without_bos(monkeypatch):
    pair = {"display": "AAPL", "symbol": "AAPL", "type": "stock", "source": "mt5"}
    d1 = _make_bars(datetime(2024, 1, 1, tzinfo=timezone.utc), 100, 24, base=90.0)
    h4 = _make_bars(datetime(2024, 2, 1, tzinfo=timezone.utc), 80, 4, base=99.0)
    h1 = _make_bars(datetime(2024, 2, 1, tzinfo=timezone.utc), 400, 1, base=100.0)
    # Bar 81 wicks TP1 but closes back below it (no confirmed break).
    h1[81].update({"high": 101.60, "low": 100.60, "close": 101.00})
    # Bar 82 wicks through TP2 but never closes beyond TP1 — without a
    # confirmed structural break this touch must NOT be credited as TP2.
    h1[82].update({"open": 101.20, "high": 102.90, "low": 101.00, "close": 101.20})

    audit_dir = Path(os.path.dirname(__file__)) / "_artifacts"
    audit_dir.mkdir(exist_ok=True)
    runtime = SimpleNamespace(
        fetch_candles=lambda _pair, tf, _limit: {"D1": d1, "H4": h4, "H1": h1}[tf],
        naked_scan_style_profile=lambda style, score_group=None, asset_type=None: (
            "intraday",
            {"min_score": 0.5, "fallback_rr": 2.0, "min_rr": 1.0, "atr_tf": "H4"},
        ),
        engine_b_regime_label=lambda *_args, **_kwargs: "TRENDING",
        AUDIT_DB=str(audit_dir / "audit.db"),
    )

    monkeypatch.setattr(backtest_runner, "_rt", lambda: runtime)
    monkeypatch.setattr(backtest_runner, "_get_slippage_for_bar", lambda *_args, **_kwargs: 0.0)
    monkeypatch.setattr(backtest_runner, "_bt_transaction_cost_r", lambda *_args, **_kwargs: 0.0)
    monkeypatch.setattr(backtest_runner, "get_pair_score_group", lambda _pair: "default")
    monkeypatch.setattr(backtest_runner, "enrich_backtest_summary", lambda result, returns: result)
    monkeypatch.setattr(backtest_runner, "record_backtest_summary", lambda *args, **kwargs: None)
    monkeypatch.setattr(backtest_runner, "calibration_report", lambda *args, **kwargs: {})
    monkeypatch.setattr(backtest_runner, "meta_report", lambda *args, **kwargs: {})
    monkeypatch.setitem(backtest_runner.CONFIG, "ENGINE_B_BT_SCALE_OUT_TP1_SIZE", 0.5)
    monkeypatch.setitem(backtest_runner.CONFIG, "ENGINE_B_BT_MOVE_SL_TO_BE_AFTER_TP1", True)
    monkeypatch.setitem(backtest_runner.CONFIG, "ENGINE_B_BT_RUNNER_ENABLED", True)
    monkeypatch.setitem(backtest_runner.CONFIG, "ENGINE_B_BT_RUNNER_REQUIRES_BREAK", True)
    monkeypatch.setattr(
        backtest_runner,
        "_format_backtest_results",
        lambda trades, pair, engine_type="NAKED", same_bar_both_hit=0, **kwargs: {
            "pair": pair["display"],
            "engine": engine_type,
            "totalTrades": len(trades),
            "trades": trades,
            "same_bar_both_hit": same_bar_both_hit,
            "winRate": 0.0,
            "profitFactor": 0.0,
            "expectancy": 0.0,
            "sqn": 0.0,
        },
    )

    import market_structure

    monkeypatch.setattr(
        market_structure.engine,
        "analyze_structure_direction",
        lambda *_args, **_kwargs: {
            "structural_verdict": "CLEAR",
            "recommended_stop_loss": 99.5,
            "recommended_take_profit": 101.25,
            "regime_state": "TRENDING",
            "order_blocks": [],
            "liquidity_sweep": False,
            "fvg_overlap": False,
            "bos_volume_confirmed": True,
            "choch_confirmed": False,
            "ob_at_zone": False,
            "bos_mtf_confirmed": False,
            "fvg_bonus": 0.0,
            "volume_strength": 0.0,
        },
    )

    def _confidence(_res, px, direction, **_kwargs):
        if direction != "LONG":
            return {"score": 0.0, "pct": 0.0, "passed": False, "max_possible": 5.0}
        return {
            "score": 2.0,
            "pct": 80.0,
            "rr": 2.0,
            "passed": True,
            "trigger_pattern": "NONE",
            "max_possible": 5.0,
            "structure_ok": True,
            "macro_ok": True,
            "zone_ok": True,
            "breakout_ok": False,
            "location_ok": True,
            "trigger_ok": True,
            "entry_ok": True,
            "room_ok": True,
            "rr_ok": True,
            "tp_side_ok": True,
            "space_ok": True,
            "execution_sl": px - 1.0,
            "execution_tp": px + 2.0,
            "execution_tp1": px + 0.75,
            "execution_tp2": px + 2.0,
            "execution_rr1": 0.75,
            "execution_rr2": 2.0,
            "rr_used_for_gate": 2.0,
            "rr_source": "atr_sl_fallback_rr_tp",
            "exit_strategy": "scale_out_structural_tp1_fallback_tp2",
            "runner_tp_requires_structural_break": True,
        }

    monkeypatch.setattr(market_structure.engine, "calculate_confidence", _confidence)

    result = backtest_runner.backtest_pair_naked(pair, style="intraday")

    assert result["totalTrades"] >= 1
    first = result["trades"][0]
    # TP1 clip banked; the TP2 wick without a confirmed break is not credited,
    # so the BE-protected runner exits at break-even instead.
    assert first["outcome"] == "BE"
    assert first["exit_strategy_path"] == "TP1_THEN_BE"
    assert first["partial_taken_tp1"] is True
    assert first["runner_break_required"] is True
    assert first["runner_break_confirmed"] is False
    # resultR blends the unrounded rr1; the record's rr1 is rounded to 2dp.
    expected_r = round(first["rr1"] * 0.5, 2)
    assert first["resultR"] == pytest.approx(expected_r, abs=0.011)


def _setup_scale_out_harness(monkeypatch, h1, signal_px=None):
    """Shared Engine B scale-out backtest harness (mirrors the tests above).

    ``signal_px``: when set, only the signal bar whose close equals this price
    produces a passing candidate — yields exactly one deterministic trade.
    """
    pair = {"display": "AAPL", "symbol": "AAPL", "type": "stock", "source": "mt5"}
    d1 = _make_bars(datetime(2024, 1, 1, tzinfo=timezone.utc), 100, 24, base=90.0)
    h4 = _make_bars(datetime(2024, 2, 1, tzinfo=timezone.utc), 80, 4, base=99.0)

    audit_dir = Path(os.path.dirname(__file__)) / "_artifacts"
    audit_dir.mkdir(exist_ok=True)
    runtime = SimpleNamespace(
        fetch_candles=lambda _pair, tf, _limit: {"D1": d1, "H4": h4, "H1": h1}[tf],
        naked_scan_style_profile=lambda style, score_group=None, asset_type=None: (
            "intraday",
            {"min_score": 0.5, "fallback_rr": 2.0, "min_rr": 1.0, "atr_tf": "H4"},
        ),
        engine_b_regime_label=lambda *_args, **_kwargs: "TRENDING",
        AUDIT_DB=str(audit_dir / "audit.db"),
    )

    monkeypatch.setattr(backtest_runner, "_rt", lambda: runtime)
    monkeypatch.setattr(backtest_runner, "_get_slippage_for_bar", lambda *_args, **_kwargs: 0.0)
    monkeypatch.setattr(backtest_runner, "_bt_transaction_cost_r", lambda *_args, **_kwargs: 0.0)
    monkeypatch.setattr(backtest_runner, "get_pair_score_group", lambda _pair: "default")
    monkeypatch.setattr(backtest_runner, "enrich_backtest_summary", lambda result, returns: result)
    monkeypatch.setattr(backtest_runner, "record_backtest_summary", lambda *args, **kwargs: None)
    monkeypatch.setattr(backtest_runner, "calibration_report", lambda *args, **kwargs: {})
    monkeypatch.setattr(backtest_runner, "meta_report", lambda *args, **kwargs: {})
    monkeypatch.setitem(backtest_runner.CONFIG, "ENGINE_B_BT_SCALE_OUT_TP1_SIZE", 0.5)
    monkeypatch.setitem(backtest_runner.CONFIG, "ENGINE_B_BT_MOVE_SL_TO_BE_AFTER_TP1", True)
    monkeypatch.setitem(backtest_runner.CONFIG, "ENGINE_B_BT_RUNNER_ENABLED", True)
    monkeypatch.setattr(
        backtest_runner,
        "_format_backtest_results",
        lambda trades, pair, engine_type="NAKED", same_bar_both_hit=0, **kwargs: {
            "pair": pair["display"],
            "engine": engine_type,
            "totalTrades": len(trades),
            "trades": trades,
            "same_bar_both_hit": same_bar_both_hit,
            "winRate": 0.0,
            "profitFactor": 0.0,
            "expectancy": 0.0,
            "sqn": 0.0,
        },
    )

    import market_structure

    monkeypatch.setattr(
        market_structure.engine,
        "analyze_structure_direction",
        lambda *_args, **_kwargs: {
            "structural_verdict": "CLEAR",
            "recommended_stop_loss": 99.5,
            "recommended_take_profit": 101.25,
            "regime_state": "TRENDING",
            "order_blocks": [],
            "liquidity_sweep": False,
            "fvg_overlap": False,
            "bos_volume_confirmed": True,
            "choch_confirmed": False,
            "ob_at_zone": False,
            "bos_mtf_confirmed": False,
            "fvg_bonus": 0.0,
            "volume_strength": 0.0,
        },
    )

    def _confidence(_res, px, direction, **_kwargs):
        if direction != "LONG" or (
            signal_px is not None and abs(px - signal_px) > 1e-9
        ):
            return {"score": 0.0, "pct": 0.0, "passed": False, "max_possible": 5.0}
        return {
            "score": 2.0,
            "pct": 80.0,
            "rr": 2.0,
            "passed": True,
            "trigger_pattern": "NONE",
            "max_possible": 5.0,
            "structure_ok": True,
            "macro_ok": True,
            "zone_ok": True,
            "breakout_ok": False,
            "location_ok": True,
            "trigger_ok": True,
            "entry_ok": True,
            "room_ok": True,
            "rr_ok": True,
            "tp_side_ok": True,
            "space_ok": True,
            "execution_sl": px - 1.0,
            "execution_tp": px + 2.0,
            "execution_tp1": px + 0.75,
            "execution_tp2": px + 2.0,
            "execution_rr1": 0.75,
            "execution_rr2": 2.0,
            "rr_used_for_gate": 2.0,
            "rr_source": "atr_sl_fallback_rr_tp",
            "exit_strategy": "scale_out_structural_tp1_fallback_tp2",
        }

    monkeypatch.setattr(market_structure.engine, "calculate_confidence", _confidence)
    return pair


def test_backtest_scale_out_runner_trail_after_tp1(monkeypatch):
    h1 = _make_bars(datetime(2024, 2, 1, tzinfo=timezone.utc), 400, 1, base=100.0)
    # Bar 81: TP1 wick banked (close back under TP1).
    h1[81].update({"high": 101.60, "low": 100.60, "close": 101.00})
    # Bar 82: strong run-up past TP2 — with adaptive_trail there is NO fixed
    # runner TP, so this must not close the runner; it ratchets the trail.
    h1[82].update({"open": 101.20, "high": 103.50, "low": 101.00, "close": 103.00})
    # Bar 83: pullback hits the trailed stop (~103.5 - 2*ATR), above entry.
    h1[83].update({"open": 102.00, "high": 102.00, "low": 100.90, "close": 101.00})

    pair = _setup_scale_out_harness(monkeypatch, h1)
    monkeypatch.setitem(
        backtest_runner.CONFIG, "ENGINE_B_EXIT_MODE_GLOBAL_DEFAULT", "adaptive_trail"
    )
    monkeypatch.setitem(backtest_runner.CONFIG, "ENGINE_B_BT_RUNNER_TRAIL_ATR_MULT", 2.0)

    result = backtest_runner.backtest_pair_naked(pair, style="intraday")

    assert result["totalTrades"] >= 1
    first = result["trades"][0]
    assert first["runner_directive"] == "runner_trail"
    assert first["exit_mode"] == "adaptive_trail"
    # No fixed TP2 in trail mode: the TP2-level wick did not close the runner.
    assert first["outcome"] != "TP2"
    assert first["exit_strategy_path"] == "TP1_THEN_TRAIL_EXIT"
    assert first["partial_taken_tp1"] is True
    # Trailed stop exited above break-even: better than a plain BE runner.
    be_only_r = round(first["rr1"] * 0.5, 2)
    assert first["resultR"] > be_only_r


def test_backtest_scale_out_runner_timed_close(monkeypatch):
    h1 = _make_bars(datetime(2024, 2, 1, tzinfo=timezone.utc), 400, 1, base=100.0)
    # Bar 81: TP1 banked; runner then meanders below TP2 until the horizon.
    h1[81].update({"high": 101.60, "low": 100.60, "close": 101.00})
    h1[82].update({"open": 101.00, "high": 101.20, "low": 100.90, "close": 101.00})
    h1[83].update({"open": 101.00, "high": 101.40, "low": 100.90, "close": 101.30})

    # Gate the signal to bar 80's close (100.80) so exactly one trade fills
    # at bar 81's open — timed closes every 2 bars would otherwise produce
    # earlier trades that never reach the crafted bars.
    pair = _setup_scale_out_harness(monkeypatch, h1, signal_px=100.80)
    monkeypatch.setitem(
        backtest_runner.CONFIG, "ENGINE_B_EXIT_MODE_GLOBAL_DEFAULT", "time_based"
    )
    monkeypatch.setitem(backtest_runner.CONFIG, "ENGINE_B_TIME_EXIT_BARS", 2)

    result = backtest_runner.backtest_pair_naked(pair, style="intraday")

    assert result["totalTrades"] >= 1
    first = result["trades"][0]
    assert first["runner_directive"] == "runner_timed"
    assert first["exit_mode"] == "time_based"
    assert first["outcome"] == "TIMEOUT"
    assert first["exit_strategy_path"] == "TP1_THEN_TIMED"
    assert first["partial_taken_tp1"] is True
    # Runner closed at the horizon bar's close (above entry), so the blended
    # result beats the TP1-only clip but stays below a full TP2 fill.
    assert first["resultR"] > 0.0
    assert first["resultR"] < round((first["rr1"] * 0.5) + (first["rr2"] * 0.5), 2)


def test_time_series_quality_reports_parse_failures_duplicates_and_order():
    times = backtest_runner.pd.to_datetime(
        [
            "2026-03-27T00:00:00+00:00",
            "bad-ts",
            "2026-03-27T00:00:00+00:00",
            "2026-03-26T20:00:00+00:00",
        ],
        utc=True,
        errors="coerce",
    )

    quality = backtest_runner._time_series_quality("H4", times)

    assert quality["label"] == "H4"
    assert quality["parse_fail"] == 1
    assert quality["duplicate"] == 1
    assert quality["monotonic"] is False


def test_live_base_risk_pct_matches_live_gateway_defaults():
    assert backtest_runner._live_base_risk_pct("forex") == 0.005
    assert backtest_runner._live_base_risk_pct("crypto") == 0.01


def test_backtest_pair_mt5_commodity_uses_mt5_intraday_fallback(monkeypatch):
    pair = {"display": "XAG/USD", "symbol": "XAGUSD", "type": "commodity", "source": "mt5"}
    d1 = _make_bars(datetime(2024, 1, 1, tzinfo=timezone.utc), 240, 24, base=24.0)
    h4 = _make_bars(datetime(2024, 2, 1, tzinfo=timezone.utc), 320, 4, base=25.0)
    h1 = _make_bars(datetime(2024, 2, 1, tzinfo=timezone.utc), 320, 1, base=25.0)
    mt5_calls = []

    def _fetch_candles(_pair, tf, _limit):
        mt5_calls.append(tf)
        return {"D1": d1, "H4": h4, "H1": h1}[tf]

    runtime = SimpleNamespace(
        fetch_candles=_fetch_candles,
        yfinance_symbol_for_pair=lambda *_args, **_kwargs: "XAGUSD.FOREX",
        fetch_yfinance=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("D1 yfinance fallback should not run")
        ),
        fetch_bt_yfinance=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Intraday yfinance fallback should not run")
        ),
        fetch_eodhd_intraday_bt=lambda *_args, **_kwargs: (None, None),
        polygon_ticker_for_pair=lambda *_args, **_kwargs: "C:XAGUSD",
        fetch_polygon=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Polygon should not run for MT5-sourced backtests")
        ),
        extract_candles=lambda candles: candles,
        max_score_for_pair=lambda _pair: (_ for _ in ()).throw(RuntimeError("stop_after_fetch")),
    )

    monkeypatch.setattr(backtest_runner, "_rt", lambda: runtime)
    monkeypatch.setattr(backtest_runner, "get_pair_score_group", lambda _pair: "default")

    with pytest.raises(RuntimeError, match="stop_after_fetch"):
        backtest_runner.backtest_pair(pair, style="intraday")

    assert mt5_calls.count("H4") == 1
    assert mt5_calls.count("H1") == 1


def test_backtest_pair_naked_mt5_commodity_uses_mt5_intraday_fallback(monkeypatch):
    pair = {"display": "XAG/USD", "symbol": "XAGUSD", "type": "commodity", "source": "mt5"}
    d1 = _make_bars(datetime(2024, 1, 1, tzinfo=timezone.utc), 240, 24, base=24.0)
    h4 = _make_bars(datetime(2024, 2, 1, tzinfo=timezone.utc), 320, 4, base=25.0)
    h1 = _make_bars(datetime(2024, 2, 1, tzinfo=timezone.utc), 320, 1, base=25.0)
    mt5_calls = []

    def _fetch_candles(_pair, tf, _limit):
        mt5_calls.append(tf)
        return {"D1": d1, "H4": h4, "H1": h1}[tf]

    runtime = SimpleNamespace(
        fetch_candles=_fetch_candles,
        yfinance_symbol_for_pair=lambda *_args, **_kwargs: "XAGUSD.FOREX",
        fetch_yfinance=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("D1 yfinance fallback should not run")
        ),
        fetch_bt_yfinance=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Intraday yfinance fallback should not run")
        ),
        fetch_eodhd_intraday_bt=lambda *_args, **_kwargs: (None, None),
        naked_scan_style_profile=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("stop_after_fetch")
        ),
    )

    monkeypatch.setattr(backtest_runner, "_rt", lambda: runtime)
    monkeypatch.setattr(backtest_runner, "get_pair_score_group", lambda _pair: "default")

    with pytest.raises(RuntimeError, match="stop_after_fetch"):
        backtest_runner.backtest_pair_naked(pair, style="intraday")

    assert mt5_calls.count("H4") == 1
    assert mt5_calls.count("H1") == 1


def test_intermarket_point_in_time_context_excludes_future_bars():
    pair = {"display": "EUR/USD", "symbol": "EURUSD", "type": "forex", "enabled": True}
    driver = {"display": "SPY", "symbol": "SPY", "type": "stock", "enabled": True}
    all_pairs = [pair, driver]

    h4_target = _make_bars(
        datetime(2024, 1, 1, tzinfo=timezone.utc),
        180,
        4,
        base=100.0,
    )
    h4_driver = _make_bars(
        datetime(2024, 1, 1, tzinfo=timezone.utc),
        180,
        4,
        base=100.0,
    )
    for i in range(100, 140):
        h4_driver[i]["close"] = 99.0 - ((i - 100) * 0.05)
    for i in range(140, 180):
        h4_driver[i]["close"] = 97.0 + ((i - 140) * 0.20)

    universe = discover_active_universe(all_pairs)

    def _fetch(_pair, _tf, _limit):
        if _pair["display"] == "EUR/USD":
            return h4_target
        if _pair["display"] == "SPY":
            return h4_driver
        return None

    store = prepare_series_store(
        universe,
        fetch_candles=_fetch,
        timeframe="H4",
        limit=220,
        config={"INTERMARKET_CONFIRMATION": {"enabled": True}},
        preloaded_candles={"EUR/USD": h4_target, "SPY": h4_driver},
    )
    cutoff_ts = pd.to_datetime(h4_driver[140]["time"], utc=True)
    ctx = build_point_in_time_context(
        pair,
        all_pairs=all_pairs,
        disabled_pairs=set(),
        etf_pairs=[],
        series_store=store,
        cutoff_ts=cutoff_ts,
        config={"INTERMARKET_CONFIRMATION": {"enabled": True}},
    )

    assert ctx is not None
    assert ctx["drivers"]
    spy_driver = next(d for d in ctx["drivers"] if d["driver"] == "SPY")
    assert spy_driver["summary"]["current"]["driverRecentChangePct"] < 0


def test_engine_c_bt_attaches_intermarket_to_v3_signal():
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "backtest_runner.py").read_text(encoding="utf-8")
    consensus_start = source.index("def backtest_pair_consensus(")
    consensus_source = source[consensus_start:]
    assert "_attach_bt_intermarket_to_v3_signal(" in consensus_source
    assert "evaluate_engine_a_v3(" in consensus_source


def test_run_v3_backtest_accepts_intermarket_provider():
    import inspect

    from engine_a_v3.backtest import run_v3_backtest

    params = inspect.signature(run_v3_backtest).parameters
    assert "intermarket_context_provider" in params


def test_backtest_pair_naked_telemetry_captures_non_zero_values(monkeypatch):
    pair = {"display": "AAPL", "symbol": "AAPL", "type": "stock", "source": "eodhd"}
    d1 = _make_bars(datetime(2024, 1, 1, tzinfo=timezone.utc), 100, 24, base=90.0)
    h4 = _make_bars(datetime(2024, 2, 1, tzinfo=timezone.utc), 70, 4, base=99.0)
    h1 = _make_bars(datetime(2024, 2, 1, tzinfo=timezone.utc), 400, 1, base=100.0)

    audit_dir = Path(os.path.dirname(__file__)) / "_artifacts"
    audit_dir.mkdir(exist_ok=True)
    runtime = SimpleNamespace(
        fetch_eodhd=lambda *_args, **_kwargs: d1,
        extract_candles=lambda candles: candles,
        fetch_candles=lambda *_args, **_kwargs: d1,
        fetch_eodhd_intraday_bt=lambda *_args, **_kwargs: (h4, h1),
        naked_scan_style_profile=lambda style, score_group=None, asset_type=None: (
            "intraday",
            {"min_score": 0.5, "fallback_rr": 2.0, "min_rr": 1.0, "atr_tf": "H4"},
        ),
        engine_b_regime_label=lambda *_args, **_kwargs: "TRENDING",
        AUDIT_DB=str(audit_dir / "audit.db"),
    )

    monkeypatch.setattr(backtest_runner, "_rt", lambda: runtime)
    monkeypatch.setattr(backtest_runner, "_get_slippage_for_bar", lambda *_args, **_kwargs: 0.0)
    monkeypatch.setattr(backtest_runner, "get_pair_score_group", lambda _pair: "default")
    monkeypatch.setitem(backtest_runner.CONFIG, "ENGINE_B_BT_SL_MODE", "structural")
    monkeypatch.setattr(
        backtest_runner,
        "calc_levels",
        lambda entry, atr, direction, ptype, regime_state=None, style="intraday": {
            "sl": entry - 1.0 if direction == "LONG" else entry + 1.0,
            "tp1": entry + 2.0 if direction == "LONG" else entry - 2.0,
            "tp2": entry + 3.0 if direction == "LONG" else entry - 3.0,
            "rr1": 2.0,
            "rr2": 3.0,
            "mults": {"sl": 1, "tp1": 2, "tp2": 3},
        },
    )
    monkeypatch.setattr(backtest_runner, "enrich_backtest_summary", lambda result, returns: result)
    monkeypatch.setattr(backtest_runner, "record_backtest_summary", lambda *args, **kwargs: None)
    monkeypatch.setattr(backtest_runner, "calibration_report", lambda *args, **kwargs: {})
    monkeypatch.setattr(backtest_runner, "meta_report", lambda *args, **kwargs: {})
    monkeypatch.setattr(backtest_runner, "_attach_research_validation_payload", lambda *args, **kwargs: None)

    intercepted_trades = []
    monkeypatch.setattr(
        backtest_runner,
        "_format_backtest_results",
        lambda trades, pair, engine_type="NAKED", same_bar_both_hit=0, **kwargs: intercepted_trades.extend(trades) or {
            "pair": pair["display"],
            "engine": engine_type,
            "totalTrades": len(trades),
            "trades": trades,
            "same_bar_both_hit": same_bar_both_hit,
            "winRate": 0.0,
            "profitFactor": 0.0,
            "expectancy": 0.0,
            "sqn": 0.0,
        },
    )

    import market_structure

    monkeypatch.setattr(
        market_structure.engine,
        "analyze_structure_direction",
        lambda *_args, **_kwargs: {
            "structural_verdict": "CLEAR",
            "recommended_stop_loss": 95.0,
            "recommended_take_profit": 120.0,
            "regime_state": "TRENDING",
            "order_blocks": [],
            "liquidity_sweep": False,
            "fvg_overlap": True,
            "bos_volume_confirmed": True,
            "bos_data": {"volume_strength": 0.88},
            "choch_confirmed": False,
            "ob_at_zone": False,
            "bos_mtf_confirmed": False,
        },
    )
    monkeypatch.setattr(
        market_structure.engine,
        "calculate_confidence",
        lambda _res, _px, direction, **_kwargs: {
            "score": 4.0,
            "gate_score": 4.0,
            "pct": 100,
            "passed": True,
            "structure_ok": True,
            "macro_ok": True,
            "zone_ok": True,
            "breakout_ok": False,
            "location_ok": True,
            "trigger_ok": True,
            "entry_ok": True,
            "room_ok": True,
            "rr_ok": True,
            "tp_side_ok": True,
            "space_ok": True,
            "trigger_pattern": "ENGULFING",
            "ob_at_zone": False,
            "bos_mtf_confirmed": False,
            "breaker_active": False,
            "execution_sl": _px - 1.0,
            "execution_tp": _px + 4.0,
            "execution_rr": 4.0,
            "structural_sl": _px - 1.0,
            "structural_tp": _px + 4.0,
            "structural_rr": 4.0,
            "rr_used_for_gate": 4.0,
            "rr_source": "structural",
            "level_mode": "structural",
            "execution_levels_valid": True,
            "execution_level_reject_reason": None,
        },
    )

    result = backtest_runner.backtest_pair_naked(pair, style="intraday")
    assert result is not None
    assert len(intercepted_trades) > 0

    trade = intercepted_trades[0]
    assert trade["fvg_bonus"] == 1.0
    assert trade["volume_strength"] == 0.88
    assert trade["checklist_struct"] is True
    assert trade["checklist_loc"] is True
    assert trade["checklist_trigger"] is True
    assert trade["trigger_pattern"] == "ENGULFING"
    assert "actual_rr" in trade
    assert trade["actual_rr"] == 2.0
    assert trade["rr_target"] == trade["actual_rr"]
    assert "selected_tp_source" in trade
    assert "selected_sl_source" in trade
    assert trade["selected_tp_source"] == "capped_to_fallback_rr"


def test_engine_b_and_c_timeout_exits_are_fee_charged():
    src = Path(backtest_runner.__file__).read_text(encoding="utf-8")

    assert 'outcome != "TIMEOUT"' not in src
    assert "_bt_transaction_cost_r(entry, sl, pair.get" in src
    assert "_bt_transaction_cost_r(entry, sl, _ptype)" in src


def test_engine_c_bt_h1_history_depth_covers_h4_span():
    src = Path(backtest_runner.__file__).read_text(encoding="utf-8")

    assert re.search(
        r'"H1",\s*20000,\s*lambda lim: _rt\(\)\.fetch_binance_paginated\(sym, "1h", lim\)',
        src,
    )
    assert "h1_limit=20000" in src
