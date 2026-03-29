from types import SimpleNamespace

import bybit_executor
import mt5_executor
from risk_engine import RiskApproval


def test_mt5_execute_rejects_stop_beyond_configured_cap(monkeypatch):
    class _FakeMT5:
        ORDER_TYPE_BUY = 0
        ORDER_TYPE_SELL = 1

        @staticmethod
        def symbol_select(_symbol, _enable):
            return True

        @staticmethod
        def symbol_info_tick(_symbol):
            return SimpleNamespace(ask=1.1000, bid=1.0998)

        @staticmethod
        def symbol_info(_symbol):
            return SimpleNamespace(digits=5, trade_stops_level=0, point=0.00001)

    monkeypatch.setattr(mt5_executor, "_get_mt5", lambda: _FakeMT5())
    monkeypatch.setattr(mt5_executor, "mt5_connect", lambda: True)
    monkeypatch.setattr(mt5_executor, "mt5_map_symbol", lambda pair: pair)

    signal = {
        "pair": "EUR/USD",
        "direction": "LONG",
        "price": 1.1000,
        "sl": 1.0450,
        "tp1": 1.1500,
        "type": "forex",
    }
    approval = RiskApproval(True, 1.0, 100.0, 0.01, 0.01, "OK")

    result = mt5_executor.mt5_execute(signal, approval)

    assert result["success"] is False
    assert result["error"].startswith("SL_TOO_FAR")


def test_bybit_execute_uses_risk_approved_volume(monkeypatch):
    calls = []

    class _FakeExchange:
        @staticmethod
        def fetch_ticker(_symbol):
            return {"ask": 1005.0, "last": 1005.0}

        @staticmethod
        def create_market_order(symbol, side, amount, params=None):
            calls.append((symbol, side, amount, params))
            return {
                "id": "order-1",
                "average": 1005.0,
                "filled": amount,
                "fee": {"cost": 0.0},
            }

    monkeypatch.setattr(bybit_executor, "_get_exchange", lambda: _FakeExchange())
    monkeypatch.setattr(bybit_executor, "bybit_map_symbol", lambda _pair: "BTC/USDT:USDT")
    monkeypatch.setattr(bybit_executor, "_ensure_leverage", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(bybit_executor, "_set_trading_stop", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        bybit_executor.telegram_notify,
        "notify_trade_opened",
        lambda **_kwargs: None,
    )

    signal = {
        "pair": "BTCUSDT",
        "direction": "LONG",
        "price": 1000.0,
        "sl": 950.0,
        "tp1": 1100.0,
        "type": "crypto",
    }
    approval = RiskApproval(True, 0.5, 25.0, 0.01, 0.01, "OK")

    result = bybit_executor.bybit_execute(signal, approval)

    assert result["success"] is True
    assert calls[0][2] == 0.5
    assert result["volume"] == 0.5


def test_bybit_execute_does_not_retry_exchange_not_available(monkeypatch):
    class ExchangeNotAvailable(Exception):
        pass

    class _FakeExchange:
        def __init__(self):
            self.calls = 0

        @staticmethod
        def fetch_ticker(_symbol):
            return {"ask": 100.0, "last": 100.0}

        def create_market_order(self, *_args, **_kwargs):
            self.calls += 1
            raise ExchangeNotAvailable("exchange unavailable")

    exchange = _FakeExchange()
    monkeypatch.setattr(bybit_executor, "_get_exchange", lambda: exchange)
    monkeypatch.setattr(bybit_executor, "bybit_map_symbol", lambda _pair: "BTC/USDT:USDT")
    monkeypatch.setattr(bybit_executor, "_ensure_leverage", lambda *_args, **_kwargs: None)

    signal = {
        "pair": "BTCUSDT",
        "direction": "LONG",
        "price": 100.0,
        "sl": 95.0,
        "tp1": 110.0,
        "type": "crypto",
    }
    approval = RiskApproval(True, 1.0, 5.0, 0.01, 0.01, "OK")

    result = bybit_executor.bybit_execute(signal, approval)

    assert result["success"] is False
    assert result["error"].startswith("ORDER_FAILED")
    assert exchange.calls == 1
