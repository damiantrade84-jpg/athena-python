from unittest.mock import patch

from tp_sl_rr_gate_policy import tp_sl_rr_gates_disabled


def test_disabled_flag_off_never_relaxes():
    cfg = {"DISABLE_TP_SL_RR_GATES": False, "EXECUTOR_MODE": "demo"}
    assert tp_sl_rr_gates_disabled(cfg, backtest=True) is False


def test_live_mode_never_relaxes_even_when_flag_on():
    cfg = {"DISABLE_TP_SL_RR_GATES": True, "EXECUTOR_MODE": "live"}
    assert tp_sl_rr_gates_disabled(cfg, backtest=True) is False


def test_demo_mode_relaxes_when_flag_on():
    cfg = {"DISABLE_TP_SL_RR_GATES": True, "EXECUTOR_MODE": "demo"}
    assert tp_sl_rr_gates_disabled(cfg) is True


def test_backtest_context_relaxes_when_flag_on():
    cfg = {"DISABLE_TP_SL_RR_GATES": True, "EXECUTOR_MODE": "live"}
    assert tp_sl_rr_gates_disabled(cfg, backtest=True) is False

    cfg_demo = {"DISABLE_TP_SL_RR_GATES": True, "EXECUTOR_MODE": "demo"}
    assert tp_sl_rr_gates_disabled(cfg_demo, backtest=True) is True


def test_guardian_skips_sl_tp_geometry_when_relaxed():
    from config import CONFIG
    from guardian import pre_trade_invariants

    signal = {
        "pair": "EUR/USD",
        "direction": "LONG",
        "type": "forex",
        "price": 1.1000,
        "sl": 1.1200,
        "tp1": 1.0800,
    }
    with patch.dict(CONFIG, {"DISABLE_TP_SL_RR_GATES": True, "EXECUTOR_MODE": "demo"}):
        ok, reason = pre_trade_invariants(signal)
    assert ok is True
    assert reason == "OK"


def test_pre_trade_check_does_not_raise_name_error():
    from config import CONFIG
    from guardian import pre_trade_check

    signal = {
        "pair": "BTC/USDT",
        "direction": "LONG",
        "type": "crypto",
        "price": 95000.0,
        "sl": 90000.0,
        "tp1": 100000.0,
    }
    account = {"balance": 10000.0, "equity": 10000.0}
    with patch.dict(
        CONFIG,
        {
            "DISABLE_TP_SL_RR_GATES": False,
            "EXECUTOR_MODE": "demo",
            "ENGINE_A_STALE_CANDLE_GUARD": False,
        },
    ):
        ok, reason = pre_trade_check(signal, [], account)
    assert ok is True
    assert reason == "OK"


def test_pre_trade_check_blocks_bad_geometry_when_gates_enabled():
    from config import CONFIG
    from guardian import pre_trade_check

    signal = {
        "pair": "BTC/USDT",
        "direction": "LONG",
        "type": "crypto",
        "price": 95000.0,
        "sl": 98000.0,
        "tp1": 100000.0,
    }
    account = {"balance": 10000.0, "equity": 10000.0}
    with patch.dict(
        CONFIG,
        {
            "DISABLE_TP_SL_RR_GATES": False,
            "EXECUTOR_MODE": "demo",
            "ENGINE_A_STALE_CANDLE_GUARD": False,
        },
    ):
        ok, reason = pre_trade_check(signal, [], account)
    assert ok is False
    assert "LONG_SL_ABOVE_ENTRY" in reason
