"""Phase 1.4–1.6: canonical attach fail-closed, Asian parse skip, probe precompute."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from market_structure import engine_b_forex_asian_session_blocks_bar
from config import CONFIG


def test_asian_session_parse_error_skips_bar(monkeypatch):
    monkeypatch.setitem(CONFIG, "ENGINE_B_FOREX_ASIAN_SESSION_SKIP_ENABLED", True)
    # Unparseable time → fail closed (skip).
    candles = [{"time": "not-a-timestamp", "close": 1.0}]
    assert engine_b_forex_asian_session_blocks_bar(candles, "forex", "EUR/USD") is True


def test_asian_session_disabled_does_not_skip_on_parse_error(monkeypatch):
    monkeypatch.setitem(CONFIG, "ENGINE_B_FOREX_ASIAN_SESSION_SKIP_ENABLED", False)
    candles = [{"time": "not-a-timestamp", "close": 1.0}]
    assert engine_b_forex_asian_session_blocks_bar(candles, "forex", "EUR/USD") is False


def test_canonical_attach_error_sets_fail_closed_flag(monkeypatch):
    from market_structure import NakedEngine

    engine = NakedEngine()

    def _boom(*_a, **_k):
        raise RuntimeError("canonical_down")

    monkeypatch.setattr(
        "engine_b_canonical_actionability.attach_canonical_actionability",
        _boom,
    )
    res = {
        "h1_sequence": "HH_HL",
        "h4_sequence": "HH_HL",
        "bos_confirmed": True,
        "liquidity_sweep": False,
        "near_active_zone": True,
        "zone_touched": True,
        "trigger_ok": True,
        "structural_verdict": "CLEAR",
        "sl": 99.0,
        "tp": 102.0,
        "rr": 2.0,
        "atr": 1.0,
        "asset_type": "forex",
        "style": "intraday",
        "active_zone_distance": 0.5,
        "room_to_opposing_zone": 2.0,
        "nearest_resistance": 103.0,
        "nearest_support": 98.0,
        "execution_sl": 99.0,
        "execution_tp": 102.0,
        "rr_used_for_gate": 2.0,
        "execution_levels_valid": True,
        "structural_tp_valid": True,
        "bos_mtf_confirmed": False,
        "engine_b_data_fidelity": {},
        "display": "EUR/USD",
    }
    conf = engine.calculate_confidence(
        res,
        current_price=100.0,
        direction="LONG",
        entry_candles=[{"close": 100.0, "high": 100.5, "low": 99.5, "open": 99.8, "vol": 1000}],
        style_profile={"min_score": 3.0, "min_rr": 1.5, "fallback_rr": 2.0},
    )
    assert conf.get("canonical_trade_ok") is False
    assert conf.get("canonical_attach_error") == "RuntimeError"


def test_independent_probe_precomputes_once(monkeypatch):
    from scanner import _engine_b_independent_direction_probe

    precompute_calls = {"n": 0}
    direction_calls = {"n": 0}

    class _FakeEngine:
        def set_registry_context(self, _symbol):
            return self

        def precompute_structure_data(self, *args, **kwargs):
            precompute_calls["n"] += 1
            return {"_fake": True, "atr": 1.0}

        def analyze_structure_direction(self, precompute, current_price, direction):
            direction_calls["n"] += 1
            assert precompute.get("_fake") is True
            return {
                "structural_verdict": "CLEAR",
                "atr": 1.0,
                "asset_type": "forex",
            }

        def calculate_confidence(self, res_b, current_price, direction, **kwargs):
            # Distinct scores so pick_directional_candidate has a clear winner.
            score = 5.0 if direction == "LONG" else 3.0
            return {"score": score, "passed": True}

        def analyze_structure(self, *args, **kwargs):
            raise AssertionError("probe must not call analyze_structure (double precompute)")

    monkeypatch.setattr(
        "scanner.engine_b_confidence_passes",
        lambda *a, **k: (True, None),
    )
    monkeypatch.setattr(
        "scanner.independent_conflict_blocks_emit",
        lambda *a, **k: False,
    )

    direction, res_b, conf_b = _engine_b_independent_direction_probe(
        {"display": "EUR/USD", "symbol": "EURUSD", "type": "forex"},
        engine=_FakeEngine(),
        d1_candles=[{}],
        h4_candles=[{}],
        h1_candles=[{}],
        entry_candles=[{}],
        current_price=1.1,
        atr=0.001,
        regime_label="TRENDING",
        style_profile={"fallback_rr": 2.0, "min_score": 3.0},
        resolved_style="intraday",
        asset_type="forex",
        d1_snap={},
        h4_snap={},
    )
    assert precompute_calls["n"] == 1
    assert direction_calls["n"] == 2
    assert direction == "LONG"
    assert res_b is not None
    assert conf_b is not None
