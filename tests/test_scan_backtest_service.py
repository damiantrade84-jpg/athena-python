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

    def bt_pair(p, style="auto", **kwargs):
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


def test_backtest_full_v3_rejects_unsupported_validation_controls():
    seen = []

    def full_bt(**kwargs):
        seen.append(kwargs)
        return {"results": []}

    out = handle_backtest_request(
        {
            "style": "swing",
            "asset_class": "crypto",
            "validation_mode": "walk_forward",
            "purge_gap": "321",
            "folds": "5",
        },
        normalize_style=lambda s: s or "auto",
        all_pairs=[],
        backtest_pair=lambda *args, **kwargs: {},
        run_full_backtest=full_bt,
        auto_toggle_pair=lambda p, r: None,
        active_pairs=[],
        allow_auto_toggle=False,
    )

    assert out == {
        "error": "ENGINE_A_V3_VALIDATION_MODE_UNSUPPORTED",
        "status": 422,
        "validation_mode": "walk_forward",
    }
    assert seen == []


def test_backtest_service_propagates_v3_validation_rejection():
    pair = {"display": "EUR/USD", "symbol": "EURUSD", "type": "forex", "enabled": True}

    out = handle_backtest_request(
        {"pair": "EUR/USD", "validation_mode": "walk_forward"},
        normalize_style=lambda style: style or "auto",
        all_pairs=[pair],
        backtest_pair=lambda *_args, **_kwargs: {
            "error": "ENGINE_A_V3_VALIDATION_MODE_UNSUPPORTED",
            "status": 422,
            "validation_mode": "walk_forward",
        },
        run_full_backtest=lambda **_kwargs: (_ for _ in ()).throw(AssertionError("unexpected full run")),
        auto_toggle_pair=lambda _pair, _result: None,
        active_pairs=[pair],
        allow_auto_toggle=False,
    )

    assert out == {
        "error": "ENGINE_A_V3_VALIDATION_MODE_UNSUPPORTED",
        "status": 422,
        "validation_mode": "walk_forward",
    }
