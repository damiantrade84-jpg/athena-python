from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from flask import Flask

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import execution


def _make_bars(count: int = 30, base: float = 1.1000) -> list[dict]:
    start = datetime(2026, 4, 1, tzinfo=timezone.utc)
    bars = []
    for idx in range(count):
        px = base + (idx * 0.0005)
        bars.append(
            {
                "time": (start + timedelta(hours=4 * idx)).isoformat(),
                "open": px,
                "high": px + 0.0010,
                "low": px - 0.0010,
                "close": px + 0.0002,
                "vol": 1000.0,
            }
        )
    return bars


def test_engine_c_scan_uses_standalone_engine_b_gate(monkeypatch):
    pair = {
        "display": "EUR/USD",
        "symbol": "EURUSD",
        "type": "forex",
        "enabled": True,
    }
    bars = _make_bars()
    captured: dict = {}

    runtime = SimpleNamespace(
        ALL_PAIRS=[pair],
        disabled_pairs=set(),
        current_btc_bias=lambda: "neutral",
        resolve_scan_style=lambda style, _pair: style,
        normalize_style=lambda style: style,
        analyze_pair=lambda _pair, _btc_bias, style="auto": {},
        naked_scan_style_profile=lambda requested_style, score_group=None: (
            requested_style,
            {"min_score": 1.1, "fallback_rr": 2.0, "min_rr": 1.0},
        ),
        CONFIG={"ENGINE_B_FOREX_STRUCTURE_TF": "D1"},
        fetch_candles=lambda _pair, _tf, _limit: list(bars),
        engine_b_regime_label=lambda _h4, _ptype, _regime=None: "TRENDING",
        insert_shadow_from_engine_c=lambda _consensus: None,
        log=SimpleNamespace(
            warning=lambda *args, **kwargs: None,
            error=lambda *args, **kwargs: None,
        ),
    )

    class DummyNakedEngine:
        def set_registry_context(self, *_args, **_kwargs):
            return self

        def analyze_structure(self, *_args, **kwargs):
            return {
                "structural_verdict": "CLEAR",
                "direction": kwargs.get("direction") or "LONG",
                "recommended_stop_loss": 1.0900,
                "recommended_take_profit": 1.1300,
            }

        def calculate_confidence(self, *_args, **_kwargs):
            return {
                "score": 2.0,
                "max_possible": 5.0,
                "pct": 40.0,
                "passed": False,
                "rr": 2.0,
                "structure_ok": True,
                "zone_ok": True,
                "trigger_ok": True,
                "location_ok": True,
            }

    def _fake_compute_consensus(**kwargs):
        captured["signal_b"] = kwargs["signal_b"]
        captured["confidence_b"] = kwargs["confidence_b"]
        return {"verdict": "NO_SIGNAL", "conviction": 0.0, "trade": False, "tier": "SKIP"}

    monkeypatch.setattr(execution, "rt", lambda: runtime)
    monkeypatch.setattr(execution, "NakedEngine", DummyNakedEngine)
    monkeypatch.setattr(
        execution,
        "scan_candle_limits",
        lambda: {"D1": len(bars), "H4": len(bars), "H1": len(bars)},
    )
    monkeypatch.setattr(
        execution,
        "calc_indicators_with_normalized",
        lambda _candles, _ptype: {"snap": {"atr": 0.0012, "close": 1.1150}},
    )
    monkeypatch.setattr(execution, "compute_consensus", _fake_compute_consensus)
    monkeypatch.setattr(execution, "get_pair_score_group", lambda _pair: "forex_majors")

    app = Flask(__name__)
    with app.test_request_context(
        "/api/engine-c-scan",
        method="POST",
        json={"assetClass": "forex", "style": "intraday"},
    ):
        response = execution.api_engine_c_scan()

    data = response.get_json()

    assert captured["signal_b"]["structural_verdict"] == "ERROR"
    assert captured["confidence_b"]["score"] == 0
    assert data["skipped"][0]["engine_b_raw"]["direction"] == "LONG"
    assert data["skipped"][0]["engine_b_raw"]["score"] == 2.0
    assert data["skipped"][0]["engine_b_status"]["eligible"] is False
    assert data["b_only"] == []
    assert len(data["skipped"]) == 1


def test_engine_c_scan_uses_style_timeframes_and_tests_both_directions(monkeypatch):
    pair = {
        "display": "AUD/NZD",
        "symbol": "AUDNZD",
        "type": "forex",
        "enabled": True,
    }
    bars_by_tf = {
        "D1": _make_bars(base=1.3000),
        "H4": _make_bars(base=1.2000),
        "H1": _make_bars(base=1.1000),
    }
    captured: dict = {"fetch_tfs": [], "analyze_calls": []}

    def _style_profile(style_name: str):
        if style_name == "swing":
            return {
                "min_score": 1.0,
                "fallback_rr": 2.0,
                "min_rr": 1.0,
                "zone_tf": "D1",
                "entry_tf": "H4",
                "atr_tf": "D1",
            }
        return {
            "min_score": 1.0,
            "fallback_rr": 2.0,
            "min_rr": 1.0,
            "zone_tf": "H4",
            "entry_tf": "H1",
            "atr_tf": "H4",
        }

    runtime = SimpleNamespace(
        ALL_PAIRS=[pair],
        disabled_pairs=set(),
        current_btc_bias=lambda: "neutral",
        resolve_scan_style=lambda style, _pair: style,
        normalize_style=lambda style: style,
        analyze_pair=lambda _pair, _btc_bias, style="auto": {
            "direction": "LONG",
            "price": 1.2345,
            "atr": 0.0020,
        },
        naked_scan_style_profile=lambda requested_style, score_group=None: (
            requested_style,
            _style_profile(requested_style),
        ),
        CONFIG={"ENGINE_B_FOREX_STRUCTURE_TF": "D1"},
        fetch_candles=lambda _pair, tf, _limit: (
            captured["fetch_tfs"].append(tf) or list(bars_by_tf[tf])
        ),
        engine_b_regime_label=lambda _candles, _ptype, _regime=None: "TRENDING",
        insert_shadow_from_engine_c=lambda _consensus: None,
        log=SimpleNamespace(
            warning=lambda *args, **kwargs: None,
            error=lambda *args, **kwargs: None,
        ),
    )

    class DummyNakedEngine:
        def set_registry_context(self, *_args, **_kwargs):
            return self

        def analyze_structure(
            self,
            _d1_candles,
            zone_candles,
            entry_candles,
            _current_price,
            direction,
            *_args,
            **_kwargs,
        ):
            captured["analyze_calls"].append(
                {
                    "direction": direction,
                    "zone_first": zone_candles[0]["close"],
                    "entry_first": entry_candles[0]["close"],
                }
            )
            if direction == "SHORT":
                return {
                    "structural_verdict": "CLEAR",
                    "recommended_stop_loss": 1.2100,
                    "recommended_take_profit": 1.1800,
                    "current_swing_sequence": "LH_LL",
                }
            return {"structural_verdict": "ERROR"}

        def calculate_confidence(self, *_args, **_kwargs):
            return {
                "score": 5.0,
                "max_possible": 6.0,
                "pct": 80.0,
                "passed": True,
                "rr": 2.0,
                "structure_ok": True,
                "zone_ok": True,
                "trigger_ok": True,
                "location_ok": True,
            }

    def _fake_compute_consensus(**kwargs):
        captured["signal_b"] = kwargs["signal_b"]
        captured["confidence_b"] = kwargs["confidence_b"]
        return {
            "verdict": "B_ONLY_SCORED",
            "conviction": 0.8,
            "trade": False,
            "tier": "HIGH",
            "direction": kwargs["signal_b"].get("direction"),
        }

    monkeypatch.setattr(execution, "rt", lambda: runtime)
    monkeypatch.setattr(execution, "NakedEngine", DummyNakedEngine)
    monkeypatch.setattr(
        execution,
        "scan_candle_limits",
        lambda: {"D1": len(bars_by_tf["D1"]), "H4": len(bars_by_tf["H4"]), "H1": len(bars_by_tf["H1"])},
    )
    monkeypatch.setattr(
        execution,
        "calc_indicators_with_normalized",
        lambda _candles, _ptype: {"snap": {"atr": 0.0012, "close": 1.1150}},
    )
    monkeypatch.setattr(execution, "compute_consensus", _fake_compute_consensus)
    monkeypatch.setattr(execution, "get_pair_score_group", lambda _pair: "forex_crosses")

    app = Flask(__name__)
    with app.test_request_context(
        "/api/engine-c-scan",
        method="POST",
        json={"assetClass": "forex", "style": "intraday"},
    ):
        response = execution.api_engine_c_scan()

    data = response.get_json()

    assert sorted(set(captured["fetch_tfs"])) == ["D1", "H4"]
    assert [call["direction"] for call in captured["analyze_calls"]] == ["LONG", "SHORT"]
    assert captured["analyze_calls"][0]["zone_first"] == bars_by_tf["D1"][0]["close"]
    assert captured["analyze_calls"][0]["entry_first"] == bars_by_tf["H4"][0]["close"]
    assert captured["signal_b"]["direction"] == "SHORT"
    assert len(data["b_only"]) == 1
