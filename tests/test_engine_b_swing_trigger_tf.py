"""Engine B swing trigger timeframe — canonical D1/H4/H1 slot contract."""

from __future__ import annotations

import os
import sys

from typing import Any

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import config
from market_structure import NakedEngine, engine_b_candles_for_tf, resolve_engine_b_h4_snap


def _series(close: float, n: int = 30) -> list[dict]:
    return [
        {
            "open": close,
            "high": close + 0.5,
            "low": close - 0.5,
            "close": close,
            "vol": 100.0,
            "volume": 100.0,
        }
        for _ in range(n)
    ]


def test_engine_b_candles_for_tf_maps_roles():
    d1, h4, h1 = _series(1), _series(2), _series(3)
    assert engine_b_candles_for_tf("D1", d1, h4, h1)[-1]["close"] == pytest.approx(1.0)
    assert engine_b_candles_for_tf("H4", d1, h4, h1)[-1]["close"] == pytest.approx(2.0)
    assert engine_b_candles_for_tf("H1", d1, h4, h1)[-1]["close"] == pytest.approx(3.0)


def test_swing_precompute_trigger_uses_h4_canonical_slots(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_STRIP_FORMING_STRUCT", False)
    d1 = _series(100.0)
    h4 = _series(150.0)
    h1 = _series(200.0)
    pair = {"display": "EUR/USD", "type": "forex", "source": "mt5"}
    pre = NakedEngine().precompute_structure_data(
        d1,
        h4,
        h1,
        150.0,
        1.0,
        style="swing",
        asset_type="forex",
        pair=pair,
    )
    assert pre.get("_error") is None
    trigger = pre["_trigger_candles"]
    assert trigger[-1]["close"] == pytest.approx(150.0)


def test_intraday_precompute_trigger_uses_h1_canonical_slots(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_STRIP_FORMING_STRUCT", False)
    d1 = _series(100.0)
    h4 = _series(150.0)
    h1 = _series(200.0)
    pair = {"display": "EUR/USD", "type": "forex", "source": "mt5"}
    pre = NakedEngine().precompute_structure_data(
        d1,
        h4,
        h1,
        200.0,
        1.0,
        style="intraday",
        asset_type="forex",
        pair=pair,
    )
    assert pre.get("_error") is None
    trigger = pre["_trigger_candles"]
    assert trigger[-1]["close"] == pytest.approx(200.0)


def test_resolve_engine_b_h4_snap_uses_h4_series(monkeypatch):
    captured: dict[str, Any] = {}

    def _fake_calc(candles, asset_type):
        captured["len"] = len(candles)
        captured["asset_type"] = asset_type
        return {"snap": {"adx": 22.0}}

    monkeypatch.setattr(
        "indicators.calc_indicators_with_normalized",
        _fake_calc,
    )
    h4 = _series(42.0)
    snap = resolve_engine_b_h4_snap(h4, "forex")
    assert captured["len"] == 30
    assert snap.get("adx") == pytest.approx(22.0)
