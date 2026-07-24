"""Test for F1: auto_trader._execute_signal calls _hydrate_execution_candle_quality.

This is the regression guard for the audit finding that the auto-trader bypassed
the candle freshness/consistency hydration step that manual / quick execute
both perform. Adding the hydrate keeps the auto-trader from sending signals to
``risk_check`` with stale ``candleFreshness`` / ``dataFreshness`` metadata.

The hydrate is observability/safety only — it does not change scoring, gates,
SL/TP levels, ATR multipliers, or execution thresholds.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


def test_auto_trader_invokes_hydrate_before_risk_check(monkeypatch):
    """auto_trader._execute_signal must call _hydrate_execution_candle_quality
    before risk_check so that stale embedded freshness metadata cannot reach
    the risk gate.
    """
    import auto_trader

    # Build a minimal AutoTrader instance and stub its dependencies.
    trader = auto_trader.AutoTrader()
    trader._kill_switch_fn = lambda: False
    trader._audit_db = ":memory:"
    trader._config_fn = lambda: {}

    signal = {
        "pair": "EUR/USD",
        "type": "forex",
        "direction": "LONG",
        "price": 1.10,
        "sl": 1.09,
        "tp1": 1.12,
        "tp2": 1.13,
        "rr1": 2.0,
        "atr": 0.005,
    }

    # Stub broker / risk / lifecycle layers used by _execute_signal so the
    # function reaches the hydrate path. The hydrate itself is monkey-patched
    # to a sentinel so we can verify it ran.
    hydrate_calls: list[dict] = []

    def fake_hydrate(sig, *, _r):
        hydrate_calls.append(sig)
        # Tag the signal so downstream stubs can confirm hydrate ran first.
        sig["_hydrate_marker"] = True

    monkeypatch.setattr(
        "execution._hydrate_execution_candle_quality", fake_hydrate
    )
    # Stub the Phase 5a pre-risk parity gates so the test stays focused on
    # hydrate ordering (the gates are covered in
    # tests/test_execution_displacement_guard.py).
    monkeypatch.setattr(
        "execution._engine_b_pre_risk_broker_price",
        lambda sig, venue, cfg: (None, {"brokerPrice": sig.get("price")}),
    )
    monkeypatch.setattr(
        "execution._reconcile_engine_b_rr_after_broker_entry",
        lambda sig, cfg: None,
    )
    monkeypatch.setattr(
        "execution._displacement_guard_block_reason",
        lambda sig, cfg: (None, {"enabled": True}),
    )
    monkeypatch.setattr("athena_runtime.rt", lambda: MagicMock(CONFIG={}, ALL_PAIRS=[]))

    fake_account = {"balance": 10000.0, "equity": 10000.0, "risk_domain": "test"}
    fake_positions = []
    fake_symbol_info = {"digits": 5, "point": 0.00001}

    # MT5 path (non-crypto). Patch the dynamic imports inside _execute_signal.
    fake_mt5 = MagicMock()
    fake_mt5.mt5_get_account.return_value = fake_account
    fake_mt5.mt5_get_positions.return_value = fake_positions
    fake_mt5.mt5_get_symbol_info.return_value = fake_symbol_info
    monkeypatch.setitem(__import__("sys").modules, "mt5_executor", fake_mt5)

    fake_risk = MagicMock()
    approval = MagicMock()
    approval.approved = True
    approval.reason = None
    approval.volume = 0.0
    approval.risk_amount = 0.0
    approval.risk_pct = 0.0
    fake_risk.risk_check.return_value = approval
    monkeypatch.setitem(__import__("sys").modules, "risk_engine", fake_risk)

    fake_guardian = MagicMock()
    fake_guardian.pre_trade_check.return_value = (False, "test_block")
    monkeypatch.setitem(__import__("sys").modules, "guardian", fake_guardian)

    fake_lifecycle = MagicMock()
    fake_lifecycle.run_managed_execution.return_value = {"success": False, "error": "test"}
    monkeypatch.setitem(__import__("sys").modules, "execution_lifecycle", fake_lifecycle)

    # _write_error path needs a DB but we want to short-circuit it.
    with patch.object(trader, "_write_error"):
        trader._execute_signal(signal, {})

    # The hydrate must have run, and it must have run before risk_check saw
    # the signal.
    assert len(hydrate_calls) == 1, "_hydrate_execution_candle_quality was not called"
    risk_call = fake_risk.risk_check.call_args
    assert risk_call is not None, "risk_check was never reached after hydrate"
    passed_signal = risk_call.kwargs.get("signal") or risk_call.args[0]
    assert passed_signal.get("_hydrate_marker") is True, (
        "risk_check received the signal before _hydrate_execution_candle_quality ran"
    )
