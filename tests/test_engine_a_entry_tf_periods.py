"""Entry-TF (M15/M30) indicator period overrides per score group."""

from __future__ import annotations

import pytest

from config import CONFIG
from engine_a_groups import ENGINE_A_KNOWN_SCORE_GROUPS
from engine_a_v3.backtest import run_v3_backtest
from factor_scoring import (
    ENTRY_TF_PERIOD_OVERRIDE_TFS,
    _base_indicator_periods,
    _resolve_entry_tf_period_override,
    _resolved_indicator_periods_for_tf,
)
from indicators import _calc_indicator_bundle
from tests.test_engine_a_v3_backtest_parity import _candles, _costs, _pair


def _make_candles(n: int = 520) -> list:
    candles = []
    price = 100.0
    for i in range(n):
        price += 0.05 if i % 7 else -0.03
        candles.append(
            {
                "open": price - 0.02,
                "high": price + 0.5,
                "low": price - 0.5,
                "close": price,
                "volume": 1000 + i,
            }
        )
    return candles


@pytest.mark.parametrize(
    "group,asset_type",
    [
        ("forex_majors", "forex"),
        ("forex_exotics", "forex"),
        ("crypto_btc", "crypto"),
        ("crypto_other", "crypto"),
        ("bond_tlt", "stock"),
        ("precious_trackers", "commodity"),
        ("energy_oil", "commodity"),
        ("us_stock_single", "stock"),
    ],
)
def test_entry_tf_periods_differ_from_base_for_m30(group, asset_type):
    base = _base_indicator_periods(group, asset_type)
    entry = _resolved_indicator_periods_for_tf(group, asset_type, "M30")
    slow = _resolved_indicator_periods_for_tf(group, asset_type, "H4")
    assert slow == base
    assert entry["ema_long"] == base["ema_long"] == 200
    assert entry != base or group == "unknown"


def test_d1_h4_h1_use_base_periods_forex_majors():
    group, asset = "forex_majors", "forex"
    base = _base_indicator_periods(group, asset)
    for tf in ("D1", "H4", "H1"):
        assert _resolved_indicator_periods_for_tf(group, asset, tf) == base
    assert base["ema_trend"] == 26
    assert base["rsi"] == 18


def test_m30_forex_majors_uses_entry_override():
    group, asset = "forex_majors", "forex"
    entry = _resolved_indicator_periods_for_tf(group, asset, "M30")
    assert entry["ema_trend"] == 8
    assert entry["ema_momentum"] == 21
    assert entry["rsi"] == 7
    assert entry["macd_fast"] == 5
    assert entry["macd_slow"] == 15
    assert entry["adx"] == 10
    assert "rsi_bounds" not in (CONFIG.get("ENGINE_A_ENTRY_TF_PERIODS") or {}).get(
        "forex_majors", {}
    )


def test_missing_entry_config_falls_back_to_base(monkeypatch):
    group, asset = "forex_majors", "forex"
    keyed = dict(CONFIG.get("ENGINE_A_ENTRY_TF_PERIODS") or {})
    monkeypatch.setitem(
        CONFIG,
        "ENGINE_A_ENTRY_TF_PERIODS",
        {k: v for k, v in keyed.items() if k != "forex_majors"},
    )
    base = _base_indicator_periods(group, asset)
    entry = _resolved_indicator_periods_for_tf(group, asset, "M30")
    assert entry == base


def test_snapshot_keys_unchanged_with_entry_periods():
    base = _base_indicator_periods("forex_majors", "forex")
    entry = _resolved_indicator_periods_for_tf("forex_majors", "forex", "M30")
    candles = _make_candles()
    slow_snap = _calc_indicator_bundle(candles, base)["ema21"]
    fast_snap = _calc_indicator_bundle(candles, entry)["ema21"]
    assert slow_snap[-1] != fast_snap[-1]
    bundle = _calc_indicator_bundle(candles, entry)
    assert "ema21" in bundle and "ema50" in bundle and "ema200" in bundle


def test_all_known_groups_have_explicit_entry_row():
    keyed = CONFIG.get("ENGINE_A_ENTRY_TF_PERIODS") or {}
    for group in ENGINE_A_KNOWN_SCORE_GROUPS:
        assert group in keyed, f"missing ENGINE_A_ENTRY_TF_PERIODS[{group!r}]"
        row = _resolve_entry_tf_period_override(group, "forex")
        assert row is not None


def test_entry_tf_override_tfs_frozen():
    # M5 is in the set: policy v4 resolves M5 as the trigger rung for the fast
    # profiles, and excluding it silently scored M5 snapshots with H4-scale
    # periods (RSI 18 / MACD 12-26 / EMA 26 on five-minute bars).
    assert ENTRY_TF_PERIOD_OVERRIDE_TFS == frozenset({"M30", "M15", "M5"})


def test_m5_uses_intraday_periods_not_h4_scale():
    group, asset = "forex_majors", "forex"
    base = _base_indicator_periods(group, asset)
    m5 = _resolved_indicator_periods_for_tf(group, asset, "M5")
    assert m5 != base
    assert m5 == _resolved_indicator_periods_for_tf(group, asset, "M15")
    assert m5["rsi"] < base["rsi"]
    assert m5["ema_trend"] < base["ema_trend"]


def test_run_v3_backtest_m30_override_reports_entry_periods(monkeypatch):
    import engine_a_v3.backtest as backtest_module
    from tests.test_engine_a_v3_backtest_parity import _bars, _trade_signal

    monkeypatch.setattr(
        backtest_module,
        "evaluate_engine_a_v3",
        lambda *args, **kwargs: _trade_signal(),
    )
    pair = _pair(
        "forex_majors", display="EUR/USD", symbol="EURUSD", asset_type="forex"
    )
    candles = _candles()
    candles["M30"] = _bars(timeframe="M30")
    result = run_v3_backtest(
        pair,
        candles,
        horizon="intraday",
        entry_tf_override="M30",
        **_costs(),
    )
    assert result.get("error") is None
    assert result["entryTimeframe"] == "M30"
    assert result["entryTfOverride"] == "M30"
    periods = result["indicatorPeriodsByTf"]
    assert periods["M30"]["rsi"] == 7
    assert periods["M30"]["ema_trend"] == 8
    assert periods["M30"]["ema_momentum"] == 21
    assert periods["H1"]["rsi"] == 18
    assert periods["H4"]["ema_trend"] == 26
    assert periods["D1"]["ema_trend"] == 26
