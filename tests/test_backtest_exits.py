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


def _live_cfg(**overrides):
    """live_trail config: entry 100, ATR 2.0 -> risk 2.0 (SL 98), TP cap 106."""
    section = {
        "atr_length": 3,
        "sl_mult": 1.0,
        "tp_mult": 3.0,
        "be_arm_r": 0.6,
        "be_buffer_r": 0.05,
        "activation_r": 1.0,
        "trail_atr_mult": 1.0,
        "trail_lookback": 3,
        "giveback_r": 0.0,
        "clear_tp_on_activation": True,
        "stagnation_fraction": 0.5,
        "stagnation_max_abs_r": 0.25,
        "max_hold_bars": {"H1": 6},
        "same_bar_policy": "sl_first",
    }
    section.update(overrides)
    return {"BACKTEST_EXIT_MODE": "live_trail", "BACKTEST_LIVE_TRAIL": section}


def _live_exit(rows, direction="LONG", **cfg_overrides):
    return calculate_backtest_exit(
        rows, 0, 100, direction, "H1", config=_live_cfg(**cfg_overrides), atr_series=[2.0] * len(rows)
    )


def test_live_trail_mode_accepted_and_defaults_exposed():
    cfg = normalized_backtest_exit_config({"BACKTEST_EXIT_MODE": "live_trail"})
    assert cfg["BACKTEST_EXIT_MODE"] == "live_trail"
    section = cfg["BACKTEST_LIVE_TRAIL"]
    assert section["atr_length"] == 14
    assert section["activation_r"] == pytest.approx(1.0)
    assert section["giveback_r"] == pytest.approx(0.0)
    assert section["clear_tp_on_activation"] is True
    assert section["max_hold_bars"]["H1"] == 24

    merged = normalized_backtest_exit_config(_live_cfg())
    assert merged["BACKTEST_LIVE_TRAIL"]["atr_length"] == 3
    assert merged["BACKTEST_LIVE_TRAIL"]["max_hold_bars"]["H1"] == 6
    assert merged["BACKTEST_LIVE_TRAIL"]["max_hold_bars"]["D1"] == 10  # defaults preserved


def test_live_trail_initial_sl_hit_is_minus_one_r():
    rows = _candles((100, 100, 99, 100), (100, 100.4, 97.9, 98.2))
    res = _live_exit(rows)
    assert res.backtest_exit_mode == "live_trail"
    assert res.exit_reason == "sl"
    assert res.exit_price == pytest.approx(98.0)
    assert res.r_multiple == pytest.approx(-1.0)


def test_live_trail_tp_cap_hit_before_activation():
    rows = _candles((100, 100, 99, 100), (100, 106.2, 99.8, 105.0))
    res = _live_exit(rows)
    assert res.exit_reason == "tp"
    assert res.exit_price == pytest.approx(106.0)
    assert res.r_multiple == pytest.approx(3.0)


def test_live_trail_activation_then_trail_stop_hit():
    rows = _candles(
        (100, 100, 99, 100),
        (100, 102.5, 99.8, 102.4),   # close_r 1.2 -> activate; trail = 102.5 - 2.0 = 100.5
        (102.4, 103.0, 100.4, 100.6),  # low 100.4 <= 100.5 -> trail stop
    )
    res = _live_exit(rows)
    assert res.exit_reason == "trail_sl"
    assert res.exit_price == pytest.approx(100.5)
    assert res.r_multiple == pytest.approx(0.25)
    assert res.metadata["activation_index"] == 1
    assert res.metadata["peak_r"] == pytest.approx(1.2)


def test_live_trail_short_activation_then_trail_stop_hit():
    rows = _candles(
        (100, 100, 99, 100),
        (100, 100.2, 97.5, 97.6),    # close_r 1.2 -> activate; trail = 97.5 + 2.0 = 99.5
        (97.6, 99.6, 97.4, 99.4),    # high 99.6 >= 99.5 -> trail stop
    )
    res = _live_exit(rows, direction="SHORT")
    assert res.exit_reason == "trail_sl"
    assert res.exit_price == pytest.approx(99.5)
    assert res.r_multiple == pytest.approx(0.25)


def test_live_trail_breakeven_arms_on_r_progress_then_protects():
    rows = _candles(
        (100, 100, 99, 100),
        (100, 101.5, 99.9, 101.4),   # close_r 0.7 >= be_arm 0.6 -> stop 100.1 (be)
        (101.4, 101.6, 99.5, 99.6),  # low 99.5 <= 100.1 -> be stop
    )
    res = _live_exit(rows)
    assert res.exit_reason == "be_sl"
    assert res.exit_price == pytest.approx(100.1)
    assert res.r_multiple == pytest.approx(0.05)


def test_live_trail_wrong_side_first_trail_is_skipped():
    # Activation bar with a spike high: trail 105 - 2 = 103 >= close 102 -> skip
    # adoption; BE stop (100.1) remains and is the stop that fires next bar.
    rows = _candles(
        (100, 100, 99, 100),
        (100, 105.0, 99.9, 102.0),
        (102, 102.5, 99.9, 100.2),
    )
    res = _live_exit(rows, tp_mult=4.0)  # keep TP cap (108) clear of the 105 spike
    assert res.exit_reason == "be_sl"
    assert res.exit_price == pytest.approx(100.1)


def test_live_trail_stagnation_closes_flat_trade():
    flat = (100, 100.3, 99.7, 100.1)
    rows = _candles((100, 100, 99, 100), flat, flat, flat)
    res = _live_exit(rows)  # max_hold 6 * 0.5 = 3 bars; |close_r| 0.05 <= 0.25
    assert res.exit_reason == "stagnation"
    assert res.exit_index == 3
    assert res.exit_price == pytest.approx(100.1)


def test_live_trail_no_stagnation_when_progress_outside_band():
    drift = (100, 101.2, 99.9, 101.0)  # close_r 0.5: above band, below be_arm/activation
    rows = _candles((100, 100, 99, 100), drift, drift, drift, drift, drift, drift)
    res = _live_exit(rows)
    assert res.exit_reason == "timeout"
    assert res.exit_index == 6
    assert res.r_multiple == pytest.approx(0.5)


def test_live_trail_giveback_close_when_enabled():
    rows = _candles(
        (100, 100, 99, 100),
        (100, 102.5, 99.8, 102.4),   # peak_r 1.2, activated, trail 100.5
        (102.4, 102.6, 101.0, 101.1),  # close_r 0.55; giveback 0.65 >= 0.6
    )
    res = _live_exit(rows, giveback_r=0.6)
    assert res.exit_reason == "giveback"
    assert res.exit_price == pytest.approx(101.1)
    assert res.r_multiple == pytest.approx(0.55)


def test_live_exit_stub_still_invalid():
    rows = _candles((100, 100, 99, 100), (100, 101, 99, 100.5))
    res = calculate_backtest_exit(rows, 0, 100, "LONG", "H1", mode="live_exit", config=_cfg())
    assert res.exit_reason == "invalid"
    assert res.metadata["failure_reason"] == "live_exit_delegates_to_existing_backtest_path"
