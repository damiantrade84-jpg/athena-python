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


class TestBybitExecutableQuoteSafety:
    def _approval(self):
        from risk_engine import RiskApproval

        return RiskApproval(True, 0.01, 10.0, 0.01, 0.01, 0.0, "OK")

    def test_long_blocks_when_ask_missing_even_if_last_exists(self, monkeypatch):
        import bybit_executor

        class Exchange:
            def fetch_ticker(self, symbol):
                return {"bid": 67000.0, "last": 67010.0, "timestamp": 1_716_200_000_000}

        monkeypatch.setattr(bybit_executor, "_get_exchange", lambda: Exchange())
        monkeypatch.setattr(bybit_executor, "bybit_map_symbol", lambda symbol: "BTC/USDT:USDT")
        monkeypatch.setattr(bybit_executor, "_ensure_leverage", lambda exchange, symbol: None)

        result = bybit_executor.bybit_execute(
            {"pair": "BTC/USDT", "direction": "LONG", "type": "crypto", "price": 67000, "sl": 66000, "tp1": 69000},
            self._approval(),
        )

        assert result["success"] is False
        assert result["error"].startswith("EXECUTABLE_QUOTE_SIDE_MISSING")

    def test_short_blocks_when_bid_missing_even_if_last_exists(self, monkeypatch):
        import bybit_executor

        class Exchange:
            def fetch_ticker(self, symbol):
                return {"ask": 67020.0, "last": 67010.0, "timestamp": 1_716_200_000_000}

        monkeypatch.setattr(bybit_executor, "_get_exchange", lambda: Exchange())
        monkeypatch.setattr(bybit_executor, "bybit_map_symbol", lambda symbol: "BTC/USDT:USDT")
        monkeypatch.setattr(bybit_executor, "_ensure_leverage", lambda exchange, symbol: None)

        result = bybit_executor.bybit_execute(
            {"pair": "BTC/USDT", "direction": "SHORT", "type": "crypto", "price": 67000, "sl": 68000, "tp1": 65000},
            self._approval(),
        )

        assert result["success"] is False
        assert result["error"].startswith("EXECUTABLE_QUOTE_SIDE_MISSING")

    def test_enabled_spread_guard_blocks_when_real_bid_ask_spread_unavailable(self, monkeypatch):
        import bybit_executor

        class Exchange:
            def fetch_ticker(self, symbol):
                return {"ask": 67020.0, "bid": None, "last": 67010.0, "timestamp": 1_716_200_000_000}

        monkeypatch.setattr(bybit_executor, "_get_exchange", lambda: Exchange())
        monkeypatch.setattr(bybit_executor, "bybit_map_symbol", lambda symbol: "BTC/USDT:USDT")
        monkeypatch.setattr(bybit_executor, "_ensure_leverage", lambda exchange, symbol: None)
        monkeypatch.setitem(bybit_executor.CONFIG, "MAX_EXECUTION_SPREAD_PCT", {"crypto": 0.001})

        result = bybit_executor.bybit_execute(
            {"pair": "BTC/USDT", "direction": "LONG", "type": "crypto", "price": 67000, "sl": 66000, "tp1": 69000},
            self._approval(),
        )

        assert result["success"] is False
        assert result["error"] == "EXECUTABLE_SPREAD_UNAVAILABLE"
