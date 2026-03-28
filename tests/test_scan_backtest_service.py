"""Backtest request routing — pair vs symbol vs full leaderboard."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from athena_app.services.scan_backtest_service import handle_backtest_request


def test_backtest_pair_field_runs_single_pair_not_full():
    pairs = [
        {"display": "EUR/AUD", "symbol": "EURAUD=X", "type": "forex", "enabled": True},
    ]
    seen = []

    def bt_pair(p, style="auto"):
        seen.append((p["symbol"], style))
        return {"symbol": p["symbol"], "sqn": 1.0}

    def full_bt(**kwargs):
        raise AssertionError("full backtest should not run when pair resolves")

    out = handle_backtest_request(
        {"pair": "EUR/AUD", "style": "auto"},
        normalize_style=lambda s: s or "auto",
        all_pairs=pairs,
        backtest_pair=bt_pair,
        run_full_backtest=full_bt,
        auto_toggle_pair=lambda p, r: None,
        active_pairs=pairs,
        allow_auto_toggle=False,
    )
    assert out["status"] == 200
    assert seen == [("EURAUD=X", "auto")]


def test_backtest_unknown_pair_returns_404():
    pairs = [{"display": "EUR/USD", "symbol": "EURUSD=X", "type": "forex"}]

    def full_bt(**kwargs):
        raise AssertionError("full backtest should not run for unknown pair")

    out = handle_backtest_request(
        {"pair": "NOT/A_REAL_PAIR"},
        normalize_style=lambda s: "auto",
        all_pairs=pairs,
        backtest_pair=lambda p, s: {},
        run_full_backtest=full_bt,
        auto_toggle_pair=lambda p, r: None,
        active_pairs=pairs,
        allow_auto_toggle=False,
    )
    assert out["status"] == 404
    assert "Unknown pair" in out["error"]
