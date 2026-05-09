"""Tests for scanner.py Engine B level fallback safety."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from scanner import _engine_b_level_pair


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
