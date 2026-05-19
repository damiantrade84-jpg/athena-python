import math

import pandas as pd
import pytest

from backtest_exits import calculate_backtest_exit, normalized_backtest_exit_config


def _candles(*bars):
    rows = []
    for i, (open_, high, low, close) in enumerate(bars):
        rows.append({"time": f"2026-01-01T{i:02d}:00:00Z", "open": open_, "high": high, "low": low, "close": close})
    return rows


def _cfg(mode="baseline_r", **overrides):
    cfg = {
        "BACKTEST_EXIT_MODE": mode,
        "BACKTEST_BASELINE_R": {"sl_r": 1.0, "tp_r": 1.5, "max_hold_bars": {"H1": 3}},
        "BACKTEST_ATR_BASELINE": {"atr_length": 3, "sl_atr": 1.0, "tp_atr": 1.5, "max_hold_bars": {"H1": 3}},
        "BACKTEST_TRIPLE_BARRIER": {
            "target_source": "atr",
            "atr_length": 3,
            "sl_mult": 1.0,
            "tp_mult": 1.5,
            "max_hold_bars": {"M15": 12, "H1": 3, "H4": 18, "D1": 10},
            "same_bar_policy": "sl_first",
        },
    }
    for k, v in overrides.items():
        cfg[k] = v
    return cfg


def test_config_isolation_does_not_modify_live_keys():
    cfg = normalized_backtest_exit_config({"BACKTEST_EXIT_MODE": "atr_baseline"})
    assert cfg["BACKTEST_EXIT_MODE"] == "atr_baseline"
    assert "SL_ATR_MULT" not in cfg
    assert "TP1_ATR_MULT" not in cfg
    assert "STYLE_ATR_MULTS" not in cfg


def test_baseline_r_long_tp_sl_and_same_bar_sl_first():
    entry_then_tp = _candles((100, 100, 100, 100), (100, 101.6, 99.5, 101.5))
    res = calculate_backtest_exit(entry_then_tp, 0, 100, "LONG", "H1", config=_cfg())
    assert res.exit_reason == "tp"
    assert res.exit_price == pytest.approx(101.5)
    assert res.r_multiple == pytest.approx(1.5)

    entry_then_sl = _candles((100, 100, 100, 100), (100, 100.4, 98.9, 99.0))
    res = calculate_backtest_exit(entry_then_sl, 0, 100, "LONG", "H1", config=_cfg())
    assert res.exit_reason == "sl"
    assert res.exit_price == pytest.approx(99.0)
    assert res.r_multiple == pytest.approx(-1.0)

    same_bar = _candles((100, 100, 100, 100), (100, 102.0, 98.9, 101.0))
    res = calculate_backtest_exit(same_bar, 0, 100, "LONG", "H1", config=_cfg())
    assert res.exit_reason == "sl"
    assert res.exit_price == pytest.approx(99.0)


def test_baseline_r_short_tp_sl_and_same_bar_sl_first():
    entry_then_tp = _candles((100, 100, 100, 100), (100, 100.5, 98.4, 98.5))
    res = calculate_backtest_exit(entry_then_tp, 0, 100, "SHORT", "H1", config=_cfg())
    assert res.exit_reason == "tp"
    assert res.exit_price == pytest.approx(98.5)
    assert res.r_multiple == pytest.approx(1.5)

    entry_then_sl = _candles((100, 100, 100, 100), (100, 101.1, 99.8, 101.0))
    res = calculate_backtest_exit(entry_then_sl, 0, 100, "SHORT", "H1", config=_cfg())
    assert res.exit_reason == "sl"
    assert res.exit_price == pytest.approx(101.0)

    same_bar = _candles((100, 100, 100, 100), (100, 101.1, 98.4, 99.0))
    res = calculate_backtest_exit(same_bar, 0, 100, "SHORT", "H1", config=_cfg())
    assert res.exit_reason == "sl"
    assert res.exit_price == pytest.approx(101.0)


def test_atr_baseline_uses_entry_atr_not_live_settings_and_invalid_atr_is_explicit():
    rows = _candles((100, 100, 99, 99.5), (99.5, 100, 99, 99.8), (99.8, 100, 99, 100), (100, 103.1, 99.5, 102))
    atr = [None, None, 2.0, 99.0]
    res = calculate_backtest_exit(rows, 2, 100, "LONG", "H1", mode="atr_baseline", config=_cfg("atr_baseline"), atr_series=atr)
    assert res.atr_at_entry == pytest.approx(2.0)
    assert res.sl_price == pytest.approx(98.0)
    assert res.tp_price == pytest.approx(103.0)
    assert res.exit_reason == "tp"

    bad = calculate_backtest_exit(rows, 2, 100, "LONG", "H1", mode="atr_baseline", config=_cfg("atr_baseline"), atr_series=[None] * 4)
    assert bad.exit_reason == "invalid"
    assert bad.metadata["failure_reason"] == "invalid_atr_at_entry"


def test_triple_barrier_tp_sl_timeout_same_bar_and_timeframe_hold():
    cfg = _cfg("triple_barrier")
    rows_tp = _candles((100, 100, 99, 100), (100, 103.1, 99.8, 102))
    res = calculate_backtest_exit(rows_tp, 0, 100, "LONG", "H1", config=cfg, atr_series=[2.0, 2.0])
    assert res.exit_reason == "tp"
    assert res.max_hold_bars == 3

    rows_sl = _candles((100, 100, 99, 100), (100, 101, 97.9, 98.1))
    res = calculate_backtest_exit(rows_sl, 0, 100, "LONG", "H1", config=cfg, atr_series=[2.0, 2.0])
    assert res.exit_reason == "sl"

    rows_same = _candles((100, 100, 99, 100), (100, 104, 97.9, 101))
    res = calculate_backtest_exit(rows_same, 0, 100, "LONG", "H1", config=cfg, atr_series=[2.0, 2.0])
    assert res.exit_reason == "sl"

    rows_timeout = _candles((100, 100, 99, 100), (100, 101, 99, 100.5), (100, 101, 99, 100.4), (100, 101, 99, 100.2), (100, 101, 99, 100.1))
    res = calculate_backtest_exit(rows_timeout, 0, 100, "LONG", "H1", config=cfg, atr_series=[2.0] * 5)
    assert res.exit_reason == "timeout"
    assert res.exit_index == 3

    res_m15 = calculate_backtest_exit(rows_timeout, 0, 100, "LONG", "M15", config=cfg, atr_series=[2.0] * 5)
    assert res_m15.max_hold_bars == 12


def test_dataframe_candles_supported_for_research_lab_path():
    df = pd.DataFrame(_candles((100, 100, 99, 100), (100, 103.1, 99.8, 102)))
    res = calculate_backtest_exit(df, 0, 100, "LONG", "H1", mode="triple_barrier", config=_cfg("triple_barrier"), atr_series=pd.Series([2.0, 2.0]))
    assert res.exit_reason == "tp"
