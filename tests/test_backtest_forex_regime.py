"""Regression guard for forex backtest regime labeling."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from regime import detect_regime


class TestBacktestForexRegime:
    def test_detect_regime_returns_regime_label_not_signal_type_for_forex(self):
        result = detect_regime(
            h4_snap={"adx": 28, "adxMomentum": "stable"},
            pair_type="forex",
        )

        assert result["label"] == "TRENDING"
        assert result["regime"] == "TRENDING"
        assert result["label"] != "TREND_PULLBACK"
