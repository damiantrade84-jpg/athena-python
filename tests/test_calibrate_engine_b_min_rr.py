"""Synthetic-dataset tests for the Engine B min_rr calibration replay tool."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from athena_research import calibrate_engine_b_min_rr as cal


def _trades(rs, rr_target=2.0, asset="crypto"):
    return [{"resultR": r, "rr_target": rr_target, "type": asset} for r in rs]


def test_compute_cell_metrics_basic():
    trades = _trades([1.0, -1.0, 2.0, -1.0, 0.5])
    m = cal.compute_cell_metrics(trades)
    assert m["trades"] == 5
    assert m["win_rate"] == pytest.approx(0.6)
    assert m["profit_factor"] == pytest.approx(3.5 / 2.0)
    assert m["expectancy_r"] == pytest.approx(0.3)
    # Equity path: 1.0, 0.0, 2.0, 1.0, 1.5 → worst peak-to-trough = 1.0
    assert m["max_drawdown_r"] == pytest.approx(1.0)


def test_compute_cell_metrics_empty_is_all_none():
    m = cal.compute_cell_metrics([])
    assert m["trades"] == 0
    assert m["win_rate"] is None
    assert m["expectancy_r"] is None


def test_recommend_floor_requires_samples_and_margin():
    floors = (1.0, 1.5, 2.0)
    # Mode B beats Mode A by 0.5R at every floor, plenty of samples.
    trades_a = _trades([0.0] * 40, rr_target=2.0)
    trades_b = _trades([0.5] * 40, rr_target=2.0)
    assert (
        cal.recommend_floor(trades_a, trades_b, floors, margin=0.1, min_samples=30)
        == 1.0
    )
    # Thin cells never produce a recommendation.
    assert (
        cal.recommend_floor(trades_a[:5], trades_b[:5], floors, margin=0.1, min_samples=30)
        is None
    )
    # No edge → no recommendation.
    assert (
        cal.recommend_floor(trades_b, trades_a, floors, margin=0.1, min_samples=30)
        is None
    )


def test_recommend_floor_picks_first_floor_where_margin_clears():
    floors = (1.0, 1.5, 2.0)
    # Low-RR trades drag Mode B down; filtering at floor 1.5 removes them.
    trades_a = _trades([0.0] * 40, rr_target=1.0) + _trades([0.0] * 40, rr_target=2.0)
    trades_b = _trades([-0.5] * 40, rr_target=1.0) + _trades([0.5] * 40, rr_target=2.0)
    rec = cal.recommend_floor(trades_a, trades_b, floors, margin=0.2, min_samples=30)
    assert rec == 1.5


def test_run_calibration_and_report_with_synthetic_backtest(monkeypatch):
    calls = []

    def _fake_backtest(pair, style=None, **_kwargs):
        runner_on = bool(cal.CONFIG.get("ENGINE_B_BT_RUNNER_ENABLED", True))
        calls.append((pair["display"], style, runner_on))
        # Mode B (runner on) yields +0.4R expectancy, Mode A breaks even.
        r = 0.4 if runner_on else 0.0
        return {"trades": _trades([r] * 40, rr_target=2.0)}

    monkeypatch.setattr(cal, "backtest_pair_naked", _fake_backtest)

    pairs = [{"display": "BTC/USDT", "symbol": "BTCUSDT", "type": "crypto", "source": "bybit"}]
    results = cal.run_calibration(
        pairs, styles=("intraday",), floors=(1.0, 1.5, 2.0), margin=0.1, min_samples=30
    )

    # Both modes ran for the style.
    assert ("BTC/USDT", "intraday", False) in calls
    assert ("BTC/USDT", "intraday", True) in calls
    style_data = results["styles"]["intraday"]
    assert style_data["mode_a"]["trades"] == 40
    assert style_data["mode_b"]["expectancy_r"] == pytest.approx(0.4)
    # Recommended floor within the swept bounds.
    assert style_data["recommended_floor"] in (1.0, 1.5, 2.0)

    report = cal.build_report(results)
    assert "# Engine B min_rr calibration report" in report
    assert "Style: intraday" in report
    assert "Recommended min_rr floor (intraday): **1.0**" in report
    assert "trail_not_simulated" in report
    # Config restored after the replay (override cleanup).
    assert cal.CONFIG.get("ENGINE_B_BT_RUNNER_ENABLED", True) is True


def test_config_overrides_are_restored_on_error(monkeypatch):
    def _boom(pair, style=None, **_kwargs):
        raise RuntimeError("backtest exploded")

    monkeypatch.setattr(cal, "backtest_pair_naked", _boom)
    before = cal.CONFIG.get("ENGINE_B_BT_RUNNER_ENABLED", True)

    pairs = [{"display": "BTC/USDT", "symbol": "BTCUSDT", "type": "crypto", "source": "bybit"}]
    results = cal.run_calibration(pairs, styles=("intraday",))

    assert results["styles"]["intraday"]["mode_a"]["trades"] == 0
    assert cal.CONFIG.get("ENGINE_B_BT_RUNNER_ENABLED", True) == before


def test_placeholder_floors_present_but_not_wired():
    """Config placeholders exist with current-value defaults; the resolver
    must not consume them yet (no behavior change this phase)."""
    import inspect

    from config import CONFIG
    import market_structure

    assert CONFIG.get("ENGINE_B_MIN_RR_INTRADAY_FLOOR") == 1.5
    assert CONFIG.get("ENGINE_B_MIN_RR_SWING_FLOOR") == 2.0
    assert "forex" in CONFIG.get("ENGINE_B_MIN_RR_BY_ASSET_CLASS", {})
    src = inspect.getsource(market_structure.resolve_engine_b_execution_levels)
    assert "ENGINE_B_MIN_RR_INTRADAY_FLOOR" not in src
    assert "ENGINE_B_MIN_RR_SWING_FLOOR" not in src
