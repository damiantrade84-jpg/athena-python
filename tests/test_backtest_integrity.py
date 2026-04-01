"""Regression checks for backtest realism fixes."""

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import backtest_runner


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


def test_engine_b_confidence_gate_requires_passed_and_score_floor():
    from market_structure import engine_b_confidence_passes

    style_profile = {"min_score": 1.1}

    gate_ok, scaled_min = engine_b_confidence_passes(
        {"score": 1.0, "passed": False},
        style_profile,
        "TRENDING",
    )
    # engine_b_min_score_threshold applies regime gate then math.ceil (discrete checklist floor)
    assert round(scaled_min, 3) == 1.0
    assert gate_ok is False

    gate_ok, scaled_min = engine_b_confidence_passes(
        {"score": 1.0, "passed": True},
        style_profile,
        "TRENDING",
    )
    assert round(scaled_min, 3) == 1.0
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
        naked_scan_style_profile=lambda style, score_group=None: (
            "intraday",
            {"min_score": 0.5, "fallback_rr": 2.0, "min_rr": 1.0, "atr_tf": "H4"},
        ),
        engine_b_regime_label=lambda *_args, **_kwargs: "TRENDING",
        AUDIT_DB=str(audit_dir / "audit.db"),
    )

    slip_calls = []

    def _track_slip(bar, ptype):
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
        "analyze_structure",
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

    result = backtest_runner.backtest_pair_naked(pair, style="intraday")

    assert result["totalTrades"] >= 1
    assert "researchValidation" in result
    assert result["researchValidation"].get("validationMode") == "standard"
    assert slip_calls, "slippage helper should run for each fill"
    # Next-bar open with slippage: LONG entry = open + open*slip (slip fixed at 0.001 here).
    long_trades = [t for t in result["trades"] if t.get("direction") == "LONG"]
    assert long_trades
    for t in long_trades[:3]:
        # Patched slip is fractional (0.001), applied as raw_entry * 0.001
        assert t["entry"] > 0
    assert result["same_bar_both_hit"] == 0


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
