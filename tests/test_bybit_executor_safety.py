"""Tests for bybit_executor.py safety fallbacks."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from types import SimpleNamespace


class TestBybitPositionRiskFallback:
    """Positions without SL must report high risk, not a fixed 2%."""

    def _make_mock_exchange(self, positions_info):
        """Build a minimal mock ccxt exchange."""
        class MockExchange:
            def fetch_positions(self, **kwargs):
                return positions_info
        return MockExchange()

    def _mock_position(self, *, symbol="BTC/USDT:USDT", contracts=0.1, entry=60000,
                       stop_loss=None, take_profit=None, side="long"):
        info = {
            "symbol": symbol.replace(":", ""),
            "size": str(contracts),
            "avgEntryPrice": str(entry),
            "side": side.capitalize(),
        }
        if stop_loss is not None:
            info["stopLoss"] = str(stop_loss)
        if take_profit is not None:
            info["takeProfit"] = str(take_profit)
        return {
            "symbol": symbol,
            "contracts": contracts,
            "entryPrice": entry,
            "side": side,
            "info": info,
            "markPrice": entry,
            "unrealizedPnl": 0,
        }

    def test_position_with_sl_uses_sl_risk(self, monkeypatch):
        """Position with SL should compute risk from SL distance."""
        import bybit_executor
        pos = self._mock_position(stop_loss=59000)
        exchange = self._make_mock_exchange([pos])
        monkeypatch.setattr(bybit_executor, "_get_exchange", lambda: exchange)

        result = bybit_executor.bybit_get_positions()
        assert result["error"] is False
        assert len(result["positions"]) == 1
        p = result["positions"][0]
        # Risk = (60000 - 59000) / 60000 * notional
        # notional = 0.1 * 60000 = 6000
        # risk = 1000/60000 * 6000 = 100
        assert p["risk_amount"] == pytest.approx(100.0, rel=1e-2)

    def test_position_without_sl_reports_high_risk(self, monkeypatch):
        """Position without SL should report high risk (not just 2%)."""
        import bybit_executor
        pos = self._mock_position(stop_loss=None)
        exchange = self._make_mock_exchange([pos])
        monkeypatch.setattr(bybit_executor, "_get_exchange", lambda: exchange)

        result = bybit_executor.bybit_get_positions()
        assert result["error"] is False
        p = result["positions"][0]
        notional = 0.1 * 60000  # 6000
        # Current code uses 2% fallback: 6000 * 0.02 = 120
        # After fix this should be much higher (e.g., >= notional * 0.5 = 3000)
        assert p["risk_amount"] >= notional * 0.5
