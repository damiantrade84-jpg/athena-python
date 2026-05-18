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
    approval = RiskApproval(True, 1.0, 100.0, 0.01, 0.01, 0.0, "OK")

    result = mt5_executor.mt5_execute(signal, approval)

    assert result["success"] is False
    assert result["error"].startswith("SL_TOO_FAR")


def test_mt5_execute_blocks_disabled_symbol_before_order_send(monkeypatch):
    send_called = False

    class _FakeMT5:
        ORDER_TYPE_BUY = 0
        ORDER_TYPE_SELL = 1
        TRADE_RETCODE_TRADE_DISABLED = 10017
        SYMBOL_TRADE_MODE_DISABLED = 0
        SYMBOL_TRADE_MODE_FULL = 4

        @staticmethod
        def symbol_select(_symbol, _enable):
            return True

        @staticmethod
        def symbol_info_tick(_symbol):
            return SimpleNamespace(ask=1.1000, bid=1.0998)

        @staticmethod
        def symbol_info(_symbol):
            return SimpleNamespace(
                digits=5,
                trade_stops_level=0,
                point=0.00001,
                trade_mode=_FakeMT5.SYMBOL_TRADE_MODE_DISABLED,
                visible=True,
            )

        @staticmethod
        def terminal_info():
            return SimpleNamespace(trade_allowed=True, tradeapi_disabled=False)

        @staticmethod
        def account_info():
            return SimpleNamespace(trade_allowed=True, trade_expert=True, trade_mode=0)

    def fake_send(_request):
        nonlocal send_called
        send_called = True
        raise AssertionError("order_send must not be reached for disabled symbol")

    monkeypatch.setattr(mt5_executor, "_get_mt5", lambda: _FakeMT5())
    monkeypatch.setattr(mt5_executor, "mt5_connect", lambda: True)
    monkeypatch.setattr(mt5_executor, "mt5_map_symbol", lambda pair: pair)
    monkeypatch.setattr(mt5_executor, "_send_order_with_filling_fallback", fake_send)

    signal = {
        "pair": "EUR/USD",
        "direction": "LONG",
        "price": 1.1000,
        "sl": 1.0950,
        "tp1": 1.1150,
        "type": "forex",
    }
    approval = RiskApproval(True, 1.0, 100.0, 0.01, 0.01, 0.0, "OK")

    result = mt5_executor.mt5_execute(signal, approval)

    assert send_called is False
    assert result["success"] is False
    assert result["error"] == "MT5_TRADE_DISABLED:symbol_trade_disabled"
    assert result["retcode"] == _FakeMT5.TRADE_RETCODE_TRADE_DISABLED
    assert result["tradeState"]["symbol_trade_mode_disabled"] is True


def test_mt5_execute_blocks_close_only_symbol_before_order_send(monkeypatch):
    send_called = False

    class _FakeMT5:
        ORDER_TYPE_BUY = 0
        ORDER_TYPE_SELL = 1
        TRADE_RETCODE_TRADE_DISABLED = 10017
        SYMBOL_TRADE_MODE_DISABLED = 0
        SYMBOL_TRADE_MODE_FULL = 4
        SYMBOL_TRADE_MODE_CLOSEONLY = 3

        @staticmethod
        def symbol_select(_symbol, _enable):
            return True

        @staticmethod
        def symbol_info_tick(_symbol):
            return SimpleNamespace(ask=1.1000, bid=1.0998)

        @staticmethod
        def symbol_info(_symbol):
            return SimpleNamespace(
                digits=5,
                trade_stops_level=0,
                point=0.00001,
                trade_mode=_FakeMT5.SYMBOL_TRADE_MODE_CLOSEONLY,
                visible=True,
            )

        @staticmethod
        def terminal_info():
            return SimpleNamespace(trade_allowed=True, tradeapi_disabled=False)

        @staticmethod
        def account_info():
            return SimpleNamespace(trade_allowed=True, trade_expert=True, trade_mode=0)

    def fake_send(_request):
        nonlocal send_called
        send_called = True
        raise AssertionError("order_send must not be reached for close-only symbol")

    monkeypatch.setattr(mt5_executor, "_get_mt5", lambda: _FakeMT5())
    monkeypatch.setattr(mt5_executor, "mt5_connect", lambda: True)
    monkeypatch.setattr(mt5_executor, "mt5_map_symbol", lambda pair: pair)
    monkeypatch.setattr(mt5_executor, "_send_order_with_filling_fallback", fake_send)

    signal = {
        "pair": "EUR/USD",
        "direction": "LONG",
        "price": 1.1000,
        "sl": 1.0950,
        "tp1": 1.1150,
        "type": "forex",
    }
    approval = RiskApproval(True, 1.0, 100.0, 0.01, 0.01, 0.0, "OK")

    result = mt5_executor.mt5_execute(signal, approval)

    assert send_called is False
    assert result["success"] is False
    assert result["error"] == "MT5_TRADE_DISABLED:symbol_close_only_no_new_positions"


def test_mt5_execute_reports_order_check_rejection_before_send(monkeypatch):
    send_called = False

    class _FakeMT5:
        ORDER_TYPE_BUY = 0
        ORDER_TYPE_SELL = 1
        TRADE_ACTION_DEAL = 10
        ORDER_TIME_GTC = 20
        ORDER_FILLING_IOC = 30
        TRADE_RETCODE_DONE = 10009
        TRADE_RETCODE_TRADE_DISABLED = 10017
        SYMBOL_TRADE_MODE_DISABLED = 0
        SYMBOL_TRADE_MODE_FULL = 4

        @staticmethod
        def symbol_select(_symbol, _enable):
            return True

        @staticmethod
        def symbol_info_tick(_symbol):
            return SimpleNamespace(ask=1.1000, bid=1.0998)

        @staticmethod
        def symbol_info(_symbol):
            return SimpleNamespace(
                digits=5,
                trade_stops_level=0,
                point=0.00001,
                volume_step=0.01,
                volume_min=0.01,
                trade_mode=_FakeMT5.SYMBOL_TRADE_MODE_FULL,
                visible=True,
            )

        @staticmethod
        def terminal_info():
            return SimpleNamespace(trade_allowed=True, tradeapi_disabled=False)

        @staticmethod
        def account_info():
            return SimpleNamespace(trade_allowed=True, trade_expert=True, trade_mode=0)

        @staticmethod
        def order_check(_request):
            return SimpleNamespace(
                retcode=_FakeMT5.TRADE_RETCODE_TRADE_DISABLED,
                comment="Trade disabled",
            )

    def fake_send(_request):
        nonlocal send_called
        send_called = True
        raise AssertionError("order_send must not be reached after order_check reject")

    monkeypatch.setattr(mt5_executor, "_get_mt5", lambda: _FakeMT5())
    monkeypatch.setattr(mt5_executor, "mt5_connect", lambda: True)
    monkeypatch.setattr(mt5_executor, "mt5_map_symbol", lambda pair: pair)
    monkeypatch.setattr(mt5_executor, "_send_order_with_filling_fallback", fake_send)
    monkeypatch.setitem(mt5_executor.CONFIG, "TIMED_EXIT", {"tp_mode": "trailing_atr"})

    signal = {
        "pair": "EUR/USD",
        "direction": "LONG",
        "price": 1.1000,
        "sl": 1.0950,
        "tp1": 1.1150,
        "type": "forex",
        "engine": "engine_a",
    }
    approval = RiskApproval(True, 1.0, 100.0, 0.01, 0.01, 0.0, "OK")

    result = mt5_executor.mt5_execute(signal, approval)

    assert send_called is False
    assert result["success"] is False
    assert result["error"] == "ORDER_CHECK_REJECTED: Trade disabled"
    assert result["retcode"] == _FakeMT5.TRADE_RETCODE_TRADE_DISABLED
    assert result["tradeState"]["terminal_trade_allowed"] is True


def test_mt5_filling_candidates_decode_symbol_flags(monkeypatch):
    class _FakeMT5:
        ORDER_FILLING_FOK = 0
        ORDER_FILLING_IOC = 1
        ORDER_FILLING_RETURN = 2

    monkeypatch.setattr(mt5_executor, "_SYMBOL_FILLING_MODES", {"NVDA.US": _FakeMT5.ORDER_FILLING_RETURN})

    candidates = mt5_executor._mt5_filling_candidates(
        _FakeMT5(),
        "NVDA.US",
        SimpleNamespace(filling_mode=3),
    )

    assert candidates == [
        _FakeMT5.ORDER_FILLING_RETURN,
        _FakeMT5.ORDER_FILLING_FOK,
        _FakeMT5.ORDER_FILLING_IOC,
    ]


def test_mt5_execute_retries_unsupported_filling_during_order_check(monkeypatch):
    send_requests = []
    checked_fillings = []

    class _FakeMT5:
        ORDER_TYPE_BUY = 0
        ORDER_TYPE_SELL = 1
        TRADE_ACTION_DEAL = 10
        ORDER_TIME_GTC = 20
        ORDER_FILLING_FOK = 0
        ORDER_FILLING_IOC = 1
        ORDER_FILLING_RETURN = 2
        TRADE_RETCODE_DONE = 10009
        SYMBOL_TRADE_MODE_DISABLED = 0
        SYMBOL_TRADE_MODE_FULL = 4

        @staticmethod
        def symbol_select(_symbol, _enable):
            return True

        @staticmethod
        def symbol_info_tick(_symbol):
            return SimpleNamespace(ask=218.49, bid=218.40)

        @staticmethod
        def symbol_info(_symbol):
            return SimpleNamespace(
                digits=2,
                trade_stops_level=0,
                point=0.01,
                volume_step=1.0,
                volume_min=1.0,
                trade_mode=_FakeMT5.SYMBOL_TRADE_MODE_FULL,
                visible=True,
                filling_mode=1,
            )

        @staticmethod
        def terminal_info():
            return SimpleNamespace(trade_allowed=True, tradeapi_disabled=False)

        @staticmethod
        def account_info():
            return SimpleNamespace(trade_allowed=True, trade_expert=True, trade_mode=0)

        @staticmethod
        def order_check(request):
            checked_fillings.append(request["type_filling"])
            if request["type_filling"] == _FakeMT5.ORDER_FILLING_IOC:
                return SimpleNamespace(retcode=10030, comment="Unsupported filling mode")
            return SimpleNamespace(retcode=0, comment="Done", margin=12.34)

    def fake_send(request):
        send_requests.append(dict(request))
        return SimpleNamespace(
            retcode=_FakeMT5.TRADE_RETCODE_DONE,
            order=12345,
            volume=request["volume"],
            price=request["price"],
            comment="filled",
        )

    monkeypatch.setattr(mt5_executor, "_get_mt5", lambda: _FakeMT5())
    monkeypatch.setattr(mt5_executor, "mt5_connect", lambda: True)
    monkeypatch.setattr(mt5_executor, "mt5_map_symbol", lambda pair: "NVDA.US")
    monkeypatch.setattr(mt5_executor, "_send_order_with_filling_fallback", fake_send)
    monkeypatch.setattr(mt5_executor, "_mt5_resolve_position_ticket", lambda *_args, **_kwargs: 12345)
    monkeypatch.setattr(mt5_executor, "_mt5_total_fee_cost", lambda *_args, **_kwargs: 0.0)
    monkeypatch.setattr(
        mt5_executor.telegram_notify,
        "notify_trade_opened",
        lambda **_kwargs: None,
    )
    monkeypatch.setitem(mt5_executor.CONFIG, "TIMED_EXIT", {"tp_mode": "trailing_atr"})
    monkeypatch.setattr(mt5_executor, "_SYMBOL_FILLING_MODES", {})

    signal = {
        "pair": "NVDA",
        "direction": "LONG",
        "price": 218.49,
        "sl": 205.51,
        "tp1": 240.05,
        "type": "stock",
        "engine": "engine_a",
        "confluenceScore": 1.97,
    }
    approval = RiskApproval(True, 77.0, 100.0, 0.01, 0.01, 0.0, "OK")

    result = mt5_executor.mt5_execute(signal, approval)

    assert result["success"] is True
    assert checked_fillings == [
        _FakeMT5.ORDER_FILLING_IOC,
        _FakeMT5.ORDER_FILLING_FOK,
    ]
    assert len(send_requests) == 1
    assert send_requests[0]["type_filling"] == _FakeMT5.ORDER_FILLING_FOK
    assert mt5_executor._SYMBOL_FILLING_MODES["NVDA.US"] == _FakeMT5.ORDER_FILLING_FOK


def test_mt5_execute_reports_request_when_order_send_rejects_after_check_ok(monkeypatch):
    class _FakeMT5:
        ORDER_TYPE_BUY = 0
        ORDER_TYPE_SELL = 1
        TRADE_ACTION_DEAL = 10
        ORDER_TIME_GTC = 20
        ORDER_FILLING_IOC = 30
        TRADE_RETCODE_DONE = 10009
        TRADE_RETCODE_TRADE_DISABLED = 10017
        SYMBOL_TRADE_MODE_DISABLED = 0
        SYMBOL_TRADE_MODE_FULL = 4

        @staticmethod
        def symbol_select(_symbol, _enable):
            return True

        @staticmethod
        def symbol_info_tick(_symbol):
            return SimpleNamespace(ask=1.1000, bid=1.0998)

        @staticmethod
        def symbol_info(_symbol):
            return SimpleNamespace(
                digits=5,
                trade_stops_level=0,
                point=0.00001,
                volume_step=0.01,
                volume_min=0.01,
                trade_mode=_FakeMT5.SYMBOL_TRADE_MODE_FULL,
                visible=True,
            )

        @staticmethod
        def terminal_info():
            return SimpleNamespace(trade_allowed=True, tradeapi_disabled=False)

        @staticmethod
        def account_info():
            return SimpleNamespace(trade_allowed=True, trade_expert=True, trade_mode=0)

        @staticmethod
        def order_check(_request):
            return SimpleNamespace(retcode=0, comment="Done", margin=12.34)

    def fake_send(request):
        return SimpleNamespace(
            retcode=_FakeMT5.TRADE_RETCODE_TRADE_DISABLED,
            order=0,
            volume=0,
            price=0,
            comment="Trade disabled",
        )

    monkeypatch.setattr(mt5_executor, "_get_mt5", lambda: _FakeMT5())
    monkeypatch.setattr(mt5_executor, "mt5_connect", lambda: True)
    monkeypatch.setattr(mt5_executor, "mt5_map_symbol", lambda pair: pair)
    monkeypatch.setattr(mt5_executor, "_send_order_with_filling_fallback", fake_send)
    monkeypatch.setitem(
        mt5_executor.CONFIG,
        "TIMED_EXIT",
        {
            "tp_mode": "trailing_atr",
            "mt5_attach_broker_tp_when_trailing_atr": True,
        },
    )

    signal = {
        "pair": "EUR/USD",
        "direction": "LONG",
        "price": 1.1000,
        "sl": 1.0950,
        "tp1": 1.1150,
        "type": "forex",
        "engine": "engine_a",
    }
    approval = RiskApproval(True, 1.0, 100.0, 0.01, 0.01, 0.0, "OK")

    result = mt5_executor.mt5_execute(signal, approval)

    assert result["success"] is False
    assert result["error"] == "ORDER_REJECTED: Trade disabled"
    assert result["retcode"] == _FakeMT5.TRADE_RETCODE_TRADE_DISABLED
    assert result["orderCheck"] == {"retcode": 0, "comment": "Done", "margin": 12.34}
    assert result["request"]["symbol"] == "EUR/USD"
    assert result["request"]["volume"] == approval.volume
    assert result["request"]["sl"] == signal["sl"]
    assert result["request"]["tp"] == signal["tp1"]
    assert result["tradeState"]["symbol_trade_mode_disabled"] is False


def test_mt5_non_scalp_trailing_atr_execute_attaches_configured_broker_tp(monkeypatch):
    sent_requests = []

    class _FakeMT5:
        ORDER_TYPE_BUY = 0
        ORDER_TYPE_SELL = 1
        TRADE_ACTION_DEAL = 10
        ORDER_TIME_GTC = 20
        ORDER_FILLING_IOC = 30
        TRADE_RETCODE_DONE = 10009

        @staticmethod
        def symbol_select(_symbol, _enable):
            return True

        @staticmethod
        def symbol_info_tick(_symbol):
            return SimpleNamespace(ask=1.1000, bid=1.0998)

        @staticmethod
        def symbol_info(_symbol):
            return SimpleNamespace(
                digits=5,
                trade_stops_level=0,
                point=0.00001,
                volume_step=0.01,
                volume_min=0.01,
            )

    def fake_send(request):
        sent_requests.append(request)
        return SimpleNamespace(
            retcode=_FakeMT5.TRADE_RETCODE_DONE,
            order=12345,
            volume=request["volume"],
            price=request["price"],
        )

    monkeypatch.setattr(mt5_executor, "_get_mt5", lambda: _FakeMT5())
    monkeypatch.setattr(mt5_executor, "mt5_connect", lambda: True)
    monkeypatch.setattr(mt5_executor, "mt5_map_symbol", lambda pair: pair)
    monkeypatch.setattr(mt5_executor, "_send_order_with_filling_fallback", fake_send)
    monkeypatch.setattr(mt5_executor, "_mt5_resolve_position_ticket", lambda *_args, **_kwargs: 12345)
    monkeypatch.setattr(mt5_executor, "_mt5_total_fee_cost", lambda *_args, **_kwargs: 0.0)
    monkeypatch.setattr(
        mt5_executor.telegram_notify,
        "notify_trade_opened",
        lambda **_kwargs: None,
    )
    monkeypatch.setitem(
        mt5_executor.CONFIG,
        "TIMED_EXIT",
        {
            "tp_mode": "trailing_atr",
            "mt5_attach_broker_tp_when_trailing_atr": True,
        },
    )

    signal = {
        "pair": "EUR/USD",
        "direction": "LONG",
        "price": 1.1000,
        "sl": 1.0950,
        "tp1": 1.1150,
        "type": "forex",
        "engine": "engine_a",
        "style": "intraday",
    }
    approval = RiskApproval(True, 1.0, 100.0, 0.01, 0.01, 0.0, "OK")

    result = mt5_executor.mt5_execute(signal, approval)

    assert result["success"] is True
    assert sent_requests[0]["sl"] == signal["sl"]
    assert sent_requests[0]["tp"] == signal["tp1"]
    assert result["brokerTp"] == signal["tp1"]


def test_mt5_non_scalp_trailing_atr_broker_tp_does_not_split_tp2(monkeypatch):
    sent_requests = []

    class _FakeMT5:
        ORDER_TYPE_BUY = 0
        ORDER_TYPE_SELL = 1
        TRADE_ACTION_DEAL = 10
        ORDER_TIME_GTC = 20
        ORDER_FILLING_IOC = 30
        TRADE_RETCODE_DONE = 10009

        @staticmethod
        def symbol_select(_symbol, _enable):
            return True

        @staticmethod
        def symbol_info_tick(_symbol):
            return SimpleNamespace(ask=1.1000, bid=1.0998)

        @staticmethod
        def symbol_info(_symbol):
            return SimpleNamespace(
                digits=5,
                trade_stops_level=0,
                point=0.00001,
                volume_step=0.01,
                volume_min=0.01,
            )

    def fake_send(request):
        sent_requests.append(request)
        return SimpleNamespace(
            retcode=_FakeMT5.TRADE_RETCODE_DONE,
            order=12345,
            volume=request["volume"],
            price=request["price"],
        )

    monkeypatch.setattr(mt5_executor, "_get_mt5", lambda: _FakeMT5())
    monkeypatch.setattr(mt5_executor, "mt5_connect", lambda: True)
    monkeypatch.setattr(mt5_executor, "mt5_map_symbol", lambda pair: pair)
    monkeypatch.setattr(mt5_executor, "_send_order_with_filling_fallback", fake_send)
    monkeypatch.setattr(mt5_executor, "_mt5_resolve_position_ticket", lambda *_args, **_kwargs: 12345)
    monkeypatch.setattr(mt5_executor, "_mt5_total_fee_cost", lambda *_args, **_kwargs: 0.0)
    monkeypatch.setattr(
        mt5_executor.telegram_notify,
        "notify_trade_opened",
        lambda **_kwargs: None,
    )
    monkeypatch.setitem(
        mt5_executor.CONFIG,
        "TIMED_EXIT",
        {
            "tp_mode": "trailing_atr",
            "mt5_attach_broker_tp_when_trailing_atr": True,
        },
    )

    signal = {
        "pair": "EUR/USD",
        "direction": "LONG",
        "price": 1.1000,
        "sl": 1.0950,
        "tp1": 1.1150,
        "tp2": 1.1250,
        "type": "forex",
        "engine": "engine_a",
        "style": "intraday",
    }
    approval = RiskApproval(True, 0.04, 100.0, 0.01, 0.01, 0.0, "OK")

    result = mt5_executor.mt5_execute(signal, approval)

    assert result["success"] is True
    assert len(sent_requests) == 1
    assert sent_requests[0]["volume"] == approval.volume
    assert sent_requests[0]["tp"] == signal["tp1"]
    assert result["tp2"] is None


def test_mt5_non_scalp_trailing_atr_can_omit_broker_tp_when_config_disabled(monkeypatch):
    sent_requests = []

    class _FakeMT5:
        ORDER_TYPE_BUY = 0
        ORDER_TYPE_SELL = 1
        TRADE_ACTION_DEAL = 10
        ORDER_TIME_GTC = 20
        ORDER_FILLING_IOC = 30
        TRADE_RETCODE_DONE = 10009

        @staticmethod
        def symbol_select(_symbol, _enable):
            return True

        @staticmethod
        def symbol_info_tick(_symbol):
            return SimpleNamespace(ask=1.1000, bid=1.0998)

        @staticmethod
        def symbol_info(_symbol):
            return SimpleNamespace(
                digits=5,
                trade_stops_level=0,
                point=0.00001,
                volume_step=0.01,
                volume_min=0.01,
            )

    def fake_send(request):
        sent_requests.append(request)
        return SimpleNamespace(
            retcode=_FakeMT5.TRADE_RETCODE_DONE,
            order=12345,
            volume=request["volume"],
            price=request["price"],
        )

    monkeypatch.setattr(mt5_executor, "_get_mt5", lambda: _FakeMT5())
    monkeypatch.setattr(mt5_executor, "mt5_connect", lambda: True)
    monkeypatch.setattr(mt5_executor, "mt5_map_symbol", lambda pair: pair)
    monkeypatch.setattr(mt5_executor, "_send_order_with_filling_fallback", fake_send)
    monkeypatch.setattr(mt5_executor, "_mt5_resolve_position_ticket", lambda *_args, **_kwargs: 12345)
    monkeypatch.setattr(mt5_executor, "_mt5_total_fee_cost", lambda *_args, **_kwargs: 0.0)
    monkeypatch.setattr(
        mt5_executor.telegram_notify,
        "notify_trade_opened",
        lambda **_kwargs: None,
    )
    monkeypatch.setitem(
        mt5_executor.CONFIG,
        "TIMED_EXIT",
        {
            "tp_mode": "trailing_atr",
            "mt5_attach_broker_tp_when_trailing_atr": False,
        },
    )

    signal = {
        "pair": "EUR/USD",
        "direction": "LONG",
        "price": 1.1000,
        "sl": 1.0950,
        "tp1": 1.1150,
        "type": "forex",
        "engine": "engine_a",
        "style": "intraday",
    }
    approval = RiskApproval(True, 1.0, 100.0, 0.01, 0.01, 0.0, "OK")

    result = mt5_executor.mt5_execute(signal, approval)

    assert result["success"] is True
    assert sent_requests[0]["sl"] == signal["sl"]
    assert "tp" not in sent_requests[0]
    assert result["brokerTp"] == 0


def test_mt5_engine_d_trailing_atr_execute_keeps_broker_tp(monkeypatch):
    sent_requests = []

    class _FakeMT5:
        ORDER_TYPE_BUY = 0
        ORDER_TYPE_SELL = 1
        TRADE_ACTION_DEAL = 10
        ORDER_TIME_GTC = 20
        ORDER_FILLING_IOC = 30
        TRADE_RETCODE_DONE = 10009

        @staticmethod
        def symbol_select(_symbol, _enable):
            return True

        @staticmethod
        def symbol_info_tick(_symbol):
            return SimpleNamespace(ask=1.1000, bid=1.0998)

        @staticmethod
        def symbol_info(_symbol):
            return SimpleNamespace(
                digits=5,
                trade_stops_level=0,
                point=0.00001,
                volume_step=0.01,
                volume_min=0.01,
            )

    def fake_send(request):
        sent_requests.append(request)
        return SimpleNamespace(
            retcode=_FakeMT5.TRADE_RETCODE_DONE,
            order=12345,
            volume=request["volume"],
            price=request["price"],
        )

    monkeypatch.setattr(mt5_executor, "_get_mt5", lambda: _FakeMT5())
    monkeypatch.setattr(mt5_executor, "mt5_connect", lambda: True)
    monkeypatch.setattr(mt5_executor, "mt5_map_symbol", lambda pair: pair)
    monkeypatch.setattr(mt5_executor, "_send_order_with_filling_fallback", fake_send)
    monkeypatch.setattr(mt5_executor, "_mt5_resolve_position_ticket", lambda *_args, **_kwargs: 12345)
    monkeypatch.setattr(mt5_executor, "_mt5_total_fee_cost", lambda *_args, **_kwargs: 0.0)
    monkeypatch.setattr(
        mt5_executor.telegram_notify,
        "notify_trade_opened",
        lambda **_kwargs: None,
    )
    monkeypatch.setitem(mt5_executor.CONFIG, "TIMED_EXIT", {"tp_mode": "trailing_atr"})

    signal = {
        "pair": "EUR/USD",
        "direction": "LONG",
        "price": 1.1000,
        "sl": 1.0950,
        "tp1": 1.1150,
        "type": "forex",
        "engine": "SCALP",
        "style": "scalp",
    }
    approval = RiskApproval(True, 1.0, 100.0, 0.01, 0.01, 0.0, "OK")

    result = mt5_executor.mt5_execute(signal, approval)

    assert result["success"] is True
    assert sent_requests[0]["sl"] == signal["sl"]
    assert sent_requests[0]["tp"] == signal["tp1"]
    assert result["brokerTp"] == signal["tp1"]


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
                "status": "closed",
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
    approval = RiskApproval(True, 0.5, 25.0, 0.01, 0.01, 0.0, "OK")

    result = bybit_executor.bybit_execute(signal, approval)

    assert result["success"] is True
    assert calls[0][2] == 0.5
    assert result["volume"] == 0.5


def test_bybit_non_scalp_trailing_atr_execute_sends_take_profit_by_default(monkeypatch):
    stop_calls = []

    class _FakeExchange:
        @staticmethod
        def fetch_ticker(_symbol):
            return {"ask": 1005.0, "last": 1005.0}

        @staticmethod
        def create_market_order(_symbol, _side, amount, params=None):
            return {
                "id": "order-1",
                "status": "closed",
                "average": 1005.0,
                "filled": amount,
                "fee": {"cost": 0.0},
            }

        @staticmethod
        def private_post_v5_position_trading_stop(params):
            stop_calls.append(params)

    monkeypatch.setattr(bybit_executor, "_get_exchange", lambda: _FakeExchange())
    monkeypatch.setattr(bybit_executor, "bybit_map_symbol", lambda _pair: "BTC/USDT:USDT")
    monkeypatch.setattr(bybit_executor, "_ensure_leverage", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        bybit_executor.telegram_notify,
        "notify_trade_opened",
        lambda **_kwargs: None,
    )
    monkeypatch.setitem(bybit_executor.CONFIG, "TIMED_EXIT", {"tp_mode": "trailing_atr"})

    signal = {
        "pair": "BTCUSDT",
        "direction": "LONG",
        "price": 1000.0,
        "sl": 950.0,
        "tp1": 1100.0,
        "type": "crypto",
        "engine": "engine_b",
        "style": "intraday",
    }
    approval = RiskApproval(True, 0.5, 25.0, 0.01, 0.01, 0.0, "OK")

    result = bybit_executor.bybit_execute(signal, approval)

    assert result["success"] is True
    assert stop_calls[0]["stopLoss"] == "950.0"
    assert stop_calls[0]["takeProfit"] == "1100.0"


def test_bybit_non_scalp_trailing_atr_execute_can_disable_take_profit(monkeypatch):
    stop_calls = []

    class _FakeExchange:
        @staticmethod
        def fetch_ticker(_symbol):
            return {"ask": 1005.0, "last": 1005.0}

        @staticmethod
        def create_market_order(_symbol, _side, amount, params=None):
            return {
                "id": "order-1",
                "status": "closed",
                "average": 1005.0,
                "filled": amount,
                "fee": {"cost": 0.0},
            }

        @staticmethod
        def private_post_v5_position_trading_stop(params):
            stop_calls.append(params)

    monkeypatch.setattr(bybit_executor, "_get_exchange", lambda: _FakeExchange())
    monkeypatch.setattr(bybit_executor, "bybit_map_symbol", lambda _pair: "BTC/USDT:USDT")
    monkeypatch.setattr(bybit_executor, "_ensure_leverage", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        bybit_executor.telegram_notify,
        "notify_trade_opened",
        lambda **_kwargs: None,
    )
    monkeypatch.setitem(
        bybit_executor.CONFIG,
        "TIMED_EXIT",
        {"tp_mode": "trailing_atr", "bybit_attach_broker_tp_when_trailing_atr": False},
    )

    signal = {
        "pair": "BTCUSDT",
        "direction": "LONG",
        "price": 1000.0,
        "sl": 950.0,
        "tp1": 1100.0,
        "type": "crypto",
        "engine": "engine_b",
        "style": "intraday",
    }
    approval = RiskApproval(True, 0.5, 25.0, 0.01, 0.01, 0.0, "OK")

    result = bybit_executor.bybit_execute(signal, approval)

    assert result["success"] is True
    assert stop_calls[0]["stopLoss"] == "950.0"
    assert "takeProfit" not in stop_calls[0]


def test_bybit_engine_d_trailing_atr_execute_keeps_take_profit(monkeypatch):
    stop_calls = []

    class _FakeExchange:
        @staticmethod
        def fetch_ticker(_symbol):
            return {"ask": 1005.0, "last": 1005.0}

        @staticmethod
        def create_market_order(_symbol, _side, amount, params=None):
            return {
                "id": "order-1",
                "status": "closed",
                "average": 1005.0,
                "filled": amount,
                "fee": {"cost": 0.0},
            }

        @staticmethod
        def private_post_v5_position_trading_stop(params):
            stop_calls.append(params)

    monkeypatch.setattr(bybit_executor, "_get_exchange", lambda: _FakeExchange())
    monkeypatch.setattr(bybit_executor, "bybit_map_symbol", lambda _pair: "BTC/USDT:USDT")
    monkeypatch.setattr(bybit_executor, "_ensure_leverage", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        bybit_executor.telegram_notify,
        "notify_trade_opened",
        lambda **_kwargs: None,
    )
    monkeypatch.setitem(bybit_executor.CONFIG, "TIMED_EXIT", {"tp_mode": "trailing_atr"})

    signal = {
        "pair": "BTCUSDT",
        "direction": "LONG",
        "price": 1000.0,
        "sl": 950.0,
        "tp1": 1100.0,
        "type": "crypto",
        "engine": "SCALP",
        "style": "scalp",
    }
    approval = RiskApproval(True, 0.5, 25.0, 0.01, 0.01, 0.0, "OK")

    result = bybit_executor.bybit_execute(signal, approval)

    assert result["success"] is True
    assert stop_calls[0]["stopLoss"] == "950.0"
    assert stop_calls[0]["takeProfit"] == "1100.0"


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
    approval = RiskApproval(True, 1.0, 5.0, 0.01, 0.01, 0.0, "OK")

    result = bybit_executor.bybit_execute(signal, approval)

    assert result["success"] is False
    assert result["error"].startswith("ORDER_FAILED")
    assert exchange.calls == 1


def test_bybit_close_position_does_not_notify_directly(monkeypatch):
    notifications = []
    orders = []

    class _FakeExchange:
        @staticmethod
        def create_market_order(symbol, side, amount, params=None):
            orders.append((symbol, side, amount, params))
            return {"id": "close-1"}

    monkeypatch.setattr(bybit_executor, "_get_exchange", lambda: _FakeExchange())
    monkeypatch.setattr(bybit_executor, "bybit_map_symbol", lambda _pair: "BTC/USDT:USDT")
    monkeypatch.setattr(
        bybit_executor.telegram_notify,
        "notify_trade_closed",
        lambda **kwargs: notifications.append(kwargs),
    )

    result = bybit_executor.bybit_close_position("BTCUSDT", "LONG", 0.5)

    assert result["success"] is True
    assert orders == [
        ("BTC/USDT:USDT", "sell", 0.5, {"reduceOnly": True, "positionIdx": 0})
    ]
    assert notifications == []


def test_bybit_reconcile_trailing_atr_missing_tp_repairs_by_default(monkeypatch):
    stop_calls = []

    class _FakeExchange:
        @staticmethod
        def fetch_positions(params=None):
            return [
                {
                    "symbol": "BTC/USDT:USDT",
                    "contracts": 1.0,
                    "info": {"stopLoss": "950.0", "takeProfit": "0"},
                }
            ]

        @staticmethod
        def private_post_v5_position_trading_stop(params):
            stop_calls.append(params)

    monkeypatch.setattr(bybit_executor, "_get_exchange", lambda: _FakeExchange())
    monkeypatch.setattr(bybit_executor, "bybit_map_symbol", lambda _pair: "BTC/USDT:USDT")
    monkeypatch.setitem(bybit_executor.CONFIG, "TIMED_EXIT", {"tp_mode": "trailing_atr"})

    result = bybit_executor.bybit_reconcile_after_open(
        {"success": True, "symbol": "BTC/USDT:USDT", "sl": 950.0, "tp": 1100.0},
        {
            "pair": "BTCUSDT",
            "sl": 950.0,
            "tp1": 1100.0,
            "engine": "engine_a",
            "style": "intraday",
        },
    )

    assert result["repaired"] is True
    assert result["takeProfitRequired"] is True
    assert stop_calls[0]["stopLoss"] == "950.0"
    assert stop_calls[0]["takeProfit"] == "1100.0"


def test_bybit_reconcile_trailing_atr_tp_can_be_disabled(monkeypatch):
    stop_calls = []

    class _FakeExchange:
        @staticmethod
        def fetch_positions(params=None):
            return [
                {
                    "symbol": "BTC/USDT:USDT",
                    "contracts": 1.0,
                    "info": {"stopLoss": "950.0", "takeProfit": "0"},
                }
            ]

        @staticmethod
        def private_post_v5_position_trading_stop(params):
            stop_calls.append(params)

    monkeypatch.setattr(bybit_executor, "_get_exchange", lambda: _FakeExchange())
    monkeypatch.setattr(bybit_executor, "bybit_map_symbol", lambda _pair: "BTC/USDT:USDT")
    monkeypatch.setitem(
        bybit_executor.CONFIG,
        "TIMED_EXIT",
        {"tp_mode": "trailing_atr", "bybit_attach_broker_tp_when_trailing_atr": False},
    )

    result = bybit_executor.bybit_reconcile_after_open(
        {"success": True, "symbol": "BTC/USDT:USDT", "sl": 950.0, "tp": 0},
        {
            "pair": "BTCUSDT",
            "sl": 950.0,
            "tp1": 1100.0,
            "engine": "engine_a",
            "style": "intraday",
        },
    )

    assert result["repaired"] is False
    assert result["takeProfitRequired"] is False
    assert stop_calls == []


def test_bybit_reconcile_restores_missing_tp_for_fixed_mode_or_engine_d(monkeypatch):
    cases = [
        ({"tp_mode": "fixed"}, "engine_a"),
        ({"tp_mode": "trailing_atr"}, "scalp"),
    ]

    for timed_exit_cfg, engine in cases:
        stop_calls = []

        class _FakeExchange:
            @staticmethod
            def fetch_positions(params=None):
                return [
                    {
                        "symbol": "BTC/USDT:USDT",
                        "contracts": 1.0,
                        "info": {"stopLoss": "950.0", "takeProfit": "0"},
                    }
                ]

            @staticmethod
            def private_post_v5_position_trading_stop(params):
                stop_calls.append(params)

        monkeypatch.setattr(bybit_executor, "_get_exchange", lambda: _FakeExchange())
        monkeypatch.setattr(bybit_executor, "bybit_map_symbol", lambda _pair: "BTC/USDT:USDT")
        monkeypatch.setitem(bybit_executor.CONFIG, "TIMED_EXIT", timed_exit_cfg)

        result = bybit_executor.bybit_reconcile_after_open(
            {"success": True, "symbol": "BTC/USDT:USDT", "sl": 950.0, "tp": 1100.0},
            {
                "pair": "BTCUSDT",
                "sl": 950.0,
                "tp1": 1100.0,
                "engine": engine,
                "style": "intraday",
            },
        )

        assert result["repaired"] is True
        assert result["takeProfitRequired"] is True
        assert stop_calls[0]["stopLoss"] == "950.0"
        assert stop_calls[0]["takeProfit"] == "1100.0"
