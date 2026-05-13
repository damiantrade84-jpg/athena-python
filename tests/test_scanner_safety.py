"""Tests for scanner.py Engine B level fallback safety."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from engine_c import ENGINE_C_AB_WEIGHTS
from scanner import (
    _a_only_auto_weight,
    _apply_engine_b_structure_ready_scan_tier,
    _engine_b_level_pair,
    _engine_b_structure_ready_watchlist_detail,
    _mark_engine_b_structure_ready_watchlist,
)


class TestEngineBLevelPair:
    """_engine_b_level_pair must not fall through to stale levels when execution_sl is 0."""

    def test_execution_sl_zero_does_not_fall_through(self):
        """execution_sl=0.0 is a valid level; must not fall through to recommended_stop_loss."""
        conf_b = {"execution_sl": 0.0, "execution_tp": 0.0}
        res_b = {"recommended_stop_loss": 1.0900, "recommended_take_profit": 1.1300}
        sl, tp = _engine_b_level_pair(conf_b, res_b)
        # The current code uses `or` chaining: conf_b.get("execution_sl") or res_b.get("execution_sl") ...
        # 0.0 is falsy, so it falls through. This test documents the bug.
        # After fix, sl should be 0.0 and tp should be 0.0.
        assert sl == pytest.approx(0.0)
        assert tp == pytest.approx(0.0)

    def test_execution_sl_present_uses_it(self):
        conf_b = {"execution_sl": 1.0850, "execution_tp": 1.1350}
        res_b = {"recommended_stop_loss": 1.0900, "recommended_take_profit": 1.1300}
        sl, tp = _engine_b_level_pair(conf_b, res_b)
        assert sl == pytest.approx(1.0850)
        assert tp == pytest.approx(1.1350)

    def test_conf_b_none_uses_res_b(self):
        sl, tp = _engine_b_level_pair(None, {"execution_sl": 1.0900, "execution_tp": 1.1300})
        assert sl == pytest.approx(1.0900)
        assert tp == pytest.approx(1.1300)

    def test_both_missing_returns_none(self):
        sl, tp = _engine_b_level_pair({}, {})
        assert sl is None
        assert tp is None


def test_a_only_auto_weight_uses_crypto_config_not_engine_c_blend_weight():
    weight = _a_only_auto_weight(
        {"display": "BTC/USDT", "symbol": "BTCUSDT", "type": "crypto"},
        {"AUTO_TRADE_A_ONLY_WEIGHT": {"default": 0.60, "crypto": 0.60}},
    )

    assert weight == pytest.approx(0.60)
    assert weight > ENGINE_C_AB_WEIGHTS["TRENDING"]["A"]
    assert weight > ENGINE_C_AB_WEIGHTS["RANGING"]["A"]
    assert round(1.0 * weight, 4) == pytest.approx(0.60)


def test_a_only_auto_weight_defaults_safe_for_missing_or_malformed_config():
    pair = {"display": "BTC/USDT", "symbol": "BTCUSDT", "type": "crypto"}

    assert _a_only_auto_weight(pair, {}) == pytest.approx(0.60)
    assert _a_only_auto_weight(pair, {"AUTO_TRADE_A_ONLY_WEIGHT": "bad"}) == pytest.approx(0.60)
    assert _a_only_auto_weight(pair, {"AUTO_TRADE_A_ONLY_WEIGHT": {"crypto": 2.5}}) == pytest.approx(1.0)
    assert _a_only_auto_weight(pair, {"AUTO_TRADE_A_ONLY_WEIGHT": {"crypto": -0.5}}) == pytest.approx(0.0)


def _structure_ready_config(enabled=True):
    return {
        "ENGINE_B_STRUCTURE_READY_WATCHLIST": {
            "ENABLED": enabled,
            "MIN_SCORE_RATIO": 0.85,
            "REQUIRE_TRIGGER_MISSING": True,
            "REQUIRE_STRUCTURE_OK": True,
            "REQUIRE_LOCATION_CONTEXT": True,
            "FOREX_GATE_NEAR_MISS_ENABLED": True,
            "FOREX_MIN_GATE_CONFIRMATIONS": 3,
        }
    }


def _triggerless_engine_b_conf():
    return {
        "score": 8.8,
        "max_possible": 10.0,
        "min_score_scaled": 9.0,
        "passed": False,
        "structure_ok": True,
        "location_ok": True,
        "zone_ok": True,
        "trigger_ok": False,
        "entry_ok": False,
        "failed_gate_names": ["trigger"],
    }


def _clear_engine_b_structure():
    return {
        "structural_verdict": "CLEAR",
        "bos_confirmed": True,
        "choch_confirmed": False,
        "ob_at_zone": True,
        "fvg_overlap": False,
    }


def test_engine_b_structure_ready_watchlist_is_default_disabled():
    detail = _engine_b_structure_ready_watchlist_detail(
        _triggerless_engine_b_conf(),
        _clear_engine_b_structure(),
        config=_structure_ready_config(enabled=False),
    )

    assert detail is None


def test_engine_b_structure_ready_watchlist_marks_scan_only_not_passed_or_executable():
    conf_b = _triggerless_engine_b_conf()
    signal = {"scanDiagnostics": []}

    detail = _mark_engine_b_structure_ready_watchlist(
        signal,
        conf_b,
        _clear_engine_b_structure(),
        config=_structure_ready_config(enabled=True),
    )

    assert detail is not None
    assert detail["execution_allowed"] is False
    assert signal["engine_b_structure_ready_watchlist"] is True
    assert signal["engine_b_execution_blocked"] is True
    assert signal["engine_b_execution_block_reason"] == "awaiting_price_action_trigger"
    assert conf_b["passed"] is False


def test_engine_b_forex_gate_near_miss_can_surface_without_execution():
    conf_b = {
        "score": 3.5,
        "max_possible": 9.0,
        "min_score_scaled": 3.0,
        "passed": False,
        "structure_ok": False,
        "location_ok": True,
        "zone_ok": True,
        "trigger_ok": True,
        "entry_ok": True,
        "room_ok": False,
        "space_gate_ok": True,
        "rr_ok": True,
        "failed_gate_names": ["struct"],
    }
    res_b = {
        "asset_type": "forex",
        "structural_verdict": "CLEAR",
        "bos_confirmed": True,
        "ob_at_zone": True,
    }

    detail = _engine_b_structure_ready_watchlist_detail(
        conf_b,
        res_b,
        config=_structure_ready_config(enabled=True),
    )

    assert detail is not None
    assert detail["forex_gate_near_miss"] is True
    assert detail["execution_allowed"] is False
    assert detail["execution_block_reason"] == "engine_b_gate_near_miss"
    assert "struct" in detail["reason"]


def test_engine_b_structure_ready_scan_tier_promotes_skip_to_watchlist_only():
    signal = {
        "scanDiagnostics": [],
        "engine_b_structure_ready_detail": {
            "reason": "Engine B structure ready; awaiting price-action trigger",
            "execution_allowed": False,
        },
    }

    tier, reason = _apply_engine_b_structure_ready_scan_tier(
        signal,
        "skip",
        "low_confluence",
        config=_structure_ready_config(enabled=True),
    )

    assert tier == "watchlist"
    assert "awaiting price-action trigger" in reason
    assert any(d.get("code") == "engine_b_structure_ready_watchlist" for d in signal["scanDiagnostics"])


def test_engine_b_structure_ready_scan_tier_does_not_override_trade_or_safety_blocks():
    trade_signal = {
        "scanDiagnostics": [],
        "engine_b_structure_ready_detail": {
            "reason": "Engine B structure ready; awaiting price-action trigger",
            "execution_allowed": False,
        },
    }
    blocked_signal = {
        "scanDiagnostics": [{"code": "event_risk", "detail": "Earnings"}],
        "engine_b_structure_ready_detail": {
            "reason": "Engine B structure ready; awaiting price-action trigger",
            "execution_allowed": False,
        },
    }

    assert _apply_engine_b_structure_ready_scan_tier(
        trade_signal,
        "trade",
        "ok",
        config=_structure_ready_config(enabled=True),
    ) == ("trade", "ok")
    assert _apply_engine_b_structure_ready_scan_tier(
        blocked_signal,
        "skip",
        "event_risk",
        config=_structure_ready_config(enabled=True),
    ) == ("skip", "event_risk")
