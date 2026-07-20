import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import backtest_runner
import engine_c
import market_structure


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


def test_engine_c_bt_levels_cap_overstretched_tp_after_actual_fill(monkeypatch):
    monkeypatch.setattr(
        backtest_runner,
        "calc_levels",
        lambda *args, **kwargs: {"sl": 95.0, "tp1": 110.0, "rr1": 2.0},
    )

    result = backtest_runner._resolve_engine_c_bt_levels_after_fill(
        actual_entry=100.0,
        consensus={"sl": 95.0, "tp": 116.0, "sl_method": "structural", "tp_method": "structural"},
        style_profile={"min_rr": 1.5, "fallback_rr": 2.0},
        resolved_style="intraday",
        direction="LONG",
        pair_type="forex",
        atr=1.0,
        max_sl_pct=0.05,
    )

    assert result["final_sl"] == 95.0
    assert result["final_tp"] == 110.0
    assert result["final_rr"] == 2.0
    assert result["selected_tp_source"] == "capped_to_fallback_rr"
    assert result["selected_sl_source"] == "structural"


def test_engine_c_bt_levels_rebuild_tp_to_min_rr_after_actual_fill(monkeypatch):
    monkeypatch.setattr(
        backtest_runner,
        "calc_levels",
        lambda *args, **kwargs: {"sl": 95.0, "tp1": 110.0, "rr1": 2.0},
    )

    result = backtest_runner._resolve_engine_c_bt_levels_after_fill(
        actual_entry=100.0,
        consensus={"sl": 95.0, "tp": 104.0, "sl_method": "atr", "tp_method": "atr"},
        style_profile={"min_rr": 1.5, "fallback_rr": 2.0},
        resolved_style="intraday",
        direction="LONG",
        pair_type="forex",
        atr=1.0,
        max_sl_pct=0.05,
    )

    assert result["final_sl"] == 95.0
    assert result["final_tp"] == 107.5
    assert result["final_rr"] == 1.5
    assert result["selected_tp_source"] == "rebuilt_to_min_rr"
    assert result["selected_sl_source"] == "consensus"


def test_engine_c_bt_levels_keep_consensus_tp_inside_style_band(monkeypatch):
    monkeypatch.setattr(
        backtest_runner,
        "calc_levels",
        lambda *args, **kwargs: {"sl": 95.0, "tp1": 110.0, "rr1": 2.0},
    )

    result = backtest_runner._resolve_engine_c_bt_levels_after_fill(
        actual_entry=100.0,
        consensus={"sl": 95.0, "tp": 108.0, "sl_method": "atr", "tp_method": "atr"},
        style_profile={"min_rr": 1.5, "fallback_rr": 2.0},
        resolved_style="intraday",
        direction="LONG",
        pair_type="forex",
        atr=1.0,
        max_sl_pct=0.05,
    )

    assert result["final_sl"] == 95.0
    assert result["final_tp"] == 108.0
    assert result["final_rr"] == 1.6
    assert result["selected_tp_source"] == "consensus_within_band"
    assert result["selected_sl_source"] == "consensus"


def test_engine_c_bt_levels_use_calc_levels_fallback_with_resolved_style(monkeypatch):
    seen = []

    def _fake_levels(entry, atr, direction, pair_type, regime_state=None, style="intraday"):
        seen.append(style)
        return {"sl": entry - 4.0, "tp1": entry + 16.0, "rr1": 4.0}

    monkeypatch.setattr(backtest_runner, "calc_levels", _fake_levels)

    result = backtest_runner._resolve_engine_c_bt_levels_after_fill(
        actual_entry=100.0,
        consensus={},
        style_profile={"min_rr": 1.5, "fallback_rr": 2.0},
        resolved_style="swing",
        direction="LONG",
        pair_type="stock",
        atr=2.0,
        max_sl_pct=0.05,
    )

    assert seen == ["swing"]
    assert result["final_sl"] == 96.0
    assert result["final_tp"] == 108.0
    assert result["final_rr"] == 2.0
    assert result["selected_tp_source"] == "calc_levels_fallback"
    assert result["selected_sl_source"] == "calc_levels_fallback"


def test_backtest_pair_consensus_uses_resolved_style_for_a_side_fallback_and_metadata(monkeypatch):
    pair = {"display": "AAPL", "symbol": "AAPL", "type": "stock", "source": "eodhd"}
    d1 = _make_bars(datetime(2023, 10, 1, tzinfo=timezone.utc), 180, 24, base=90.0)
    h4 = _make_bars(datetime(2024, 2, 1, tzinfo=timezone.utc), 70, 4, base=99.0)
    h1 = _make_bars(datetime(2024, 2, 1, tzinfo=timezone.utc), 300, 1, base=100.0)

    runtime = SimpleNamespace(
        fetch_eodhd=lambda *_args, **_kwargs: d1,
        extract_candles=lambda candles: candles,
        fetch_candles=lambda *_args, **_kwargs: d1,
        fetch_eodhd_intraday_bt=lambda *_args, **_kwargs: (h4, h1),
        naked_scan_style_profile=lambda *_args, **_kwargs: (
            "swing",
            {
                "min_rr": 1.5,
                "fallback_rr": 2.0,
                "zone_tf": "H4",
                "entry_tf": "H4",
                "atr_tf": "H4",
            },
        ),
        engine_b_regime_label=lambda *_args, **_kwargs: "TRENDING",
        atr_for_levels=lambda *_args, **kwargs: kwargs["style"] and 1.25,
        AUDIT_DB="ignored.db",
    )

    atr_styles = []
    level_styles = []
    notes_seen = []

    def _track_atr_for_levels(d1i, h4i, h1i, pair=None, style="intraday"):
        atr_styles.append(style)
        return 1.25

    def _track_calc_levels(entry, atr, direction, pair_type, regime_state=None, style="intraday"):
        level_styles.append(style)
        return {
            "sl": entry - 1.0 if direction == "LONG" else entry + 1.0,
            "tp1": entry + 2.0 if direction == "LONG" else entry - 2.0,
            "tp2": entry + 3.0 if direction == "LONG" else entry - 3.0,
            "rr1": 2.0,
            "rr2": 3.0,
        }

    class _FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, _sql, params):
            notes_seen.append(params[-1])

        def commit(self):
            return None

    monkeypatch.setattr(backtest_runner, "_rt", lambda: runtime)
    runtime.atr_for_levels = _track_atr_for_levels
    monkeypatch.setattr(backtest_runner, "get_pair_score_group", lambda _pair: "default")
    monkeypatch.setitem(backtest_runner.CONFIG, "H4_CANDLES", 60)
    monkeypatch.setitem(backtest_runner.CONFIG, "H1_CANDLES", 60)
    monkeypatch.setitem(backtest_runner.CONFIG, "D1_CANDLES", 60)
    monkeypatch.setattr(
        backtest_runner,
        "backtest_bar_validation_state",
        lambda *_args, **_kwargs: {"skip": False, "oos_label": False, "wf_fold": 0},
    )
    monkeypatch.setattr(backtest_runner, "calc_indicators_with_normalized", lambda *_args, **_kwargs: {"snap": {}})
    monkeypatch.setattr(backtest_runner, "calc_fib", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(backtest_runner, "calc_fib_proximity", lambda *_args, **_kwargs: 0.0)
    monkeypatch.setattr(backtest_runner, "calc_confluence", lambda *_args, **_kwargs: {
        "score": 1.2,
        "maxScoreOverride": 3.0,
        "direction": "LONG",
        "regime": {"label": "TRENDING", "state": "TRENDING"},
        "factor_scores": {},
    })
    monkeypatch.setattr(backtest_runner, "_bt_btc_bias", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(backtest_runner, "build_oi_context_for_factor_scoring", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(backtest_runner, "calc_levels", _track_calc_levels)
    monkeypatch.setattr(backtest_runner, "_format_backtest_results", lambda trades, pair, **_kwargs: {
        "pair": pair["display"],
        "totalTrades": len(trades),
        "trades": trades,
        "winRate": 0.0,
        "profitFactor": 0.0,
        "expectancy": 0.0,
        "sqn": 0.0,
    })
    monkeypatch.setattr(backtest_runner, "_attach_research_validation_payload", lambda result, *_args, **_kwargs: result.update({"researchValidation": {"validationMode": "standard"}}))
    monkeypatch.setattr(backtest_runner, "calibration_report", lambda *args, **kwargs: {})
    monkeypatch.setattr(backtest_runner, "meta_report", lambda *args, **kwargs: {})
    monkeypatch.setattr(sqlite3, "connect", lambda *args, **kwargs: _FakeConnection())
    monkeypatch.setattr(engine_c, "compute_consensus", lambda **_kwargs: {"trade": False})

    class _FakeNakedEngine:
        def analyze_structure(self, *_args, **_kwargs):
            return {"structural_verdict": "CLEAR", "order_blocks": []}

        def calculate_confidence(self, *_args, **_kwargs):
            return {"score": 2.0, "pct": 80.0, "passed": True, "rr": 2.0}

    monkeypatch.setattr(market_structure, "NakedEngine", _FakeNakedEngine)

    result = backtest_runner.backtest_pair_consensus(pair, style="auto")

    assert atr_styles
    assert level_styles
    assert set(atr_styles) == {"swing"}
    assert set(level_styles) == {"swing"}
    assert result["btStyle"] == "swing"
    assert result["btStyleRequested"] == "auto"
    assert notes_seen
    assert all("engine=c;style=swing;" in note for note in notes_seen)
