"""Regression tests for aggressive bug-sweep fixes."""

from __future__ import annotations

import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


def _load_athena_module():
    path = Path(__file__).resolve().parents[1] / "athena.py"
    spec = spec_from_file_location("athena_bug_sweep_module", path)
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_bt_min_api_rejects_backtest_chain_flag():
    athena_module = _load_athena_module()
    client = athena_module.app.test_client()
    resp = client.post(
        "/api/bt-min",
        json={"backtest_use_bt_min_thresholds": True},
    )
    assert resp.status_code == 400
    data = resp.get_json()
    assert "retired" in str(data.get("error", "")).lower()


def test_bt_min_get_does_not_expose_dual_threshold_chain():
    athena_module = _load_athena_module()
    client = athena_module.app.test_client()
    resp = client.get("/api/bt-min")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data.get("backtest_uses_bt_min_chain") is False
    assert data.get("backtest_use_bt_min_thresholds") is False
    assert "live_class" in data
    assert "score_group_thresholds" in data
    assert "bt_min" not in data


def test_bt_min_post_asset_class_updates_score_group_gate(monkeypatch):
    from scoring import get_score_threshold

    athena_module = _load_athena_module()
    monkeypatch.setitem(
        athena_module.CONFIG,
        "ENGINE_A_SCORE_GROUP_THRESHOLDS",
        {
            "forex_majors": 2.1,
            "forex_crosses": 2.1,
            "forex_exotics": 1.7,
            "forex_other": 2.1,
        },
    )
    client = athena_module.app.test_client()
    resp = client.post("/api/bt-min", json={"forex": 2.55})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    data = resp.get_json()
    assert data.get("saved") is True
    assert data["score_group_thresholds"]["forex_majors"] == 2.55
    assert get_score_threshold({"display": "EUR/USD", "type": "forex"}) == 2.55
