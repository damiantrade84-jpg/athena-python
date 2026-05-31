from auto_trader import AutoTrader


def test_auto_trader_configure_skips_timed_exit_monitor_in_diagnostic_mode(monkeypatch):
    calls = []
    monkeypatch.setenv("ATHENA_DIAGNOSTIC_MODE", "1")
    monkeypatch.setattr("auto_trader.start_monitor", lambda *args: calls.append(args))

    trader = AutoTrader()
    trader.configure(
        run_scan_fn=lambda **_kwargs: {},
        kill_switch_fn=lambda: False,
        test_mode_fn=lambda: True,
        audit_db="audit.db",
        config_fn=lambda: {},
    )

    assert calls == []


def test_auto_trader_configure_starts_timed_exit_monitor_without_diagnostic_mode(monkeypatch):
    calls = []
    monkeypatch.delenv("ATHENA_DIAGNOSTIC_MODE", raising=False)
    monkeypatch.setattr("auto_trader.start_monitor", lambda *args: calls.append(args))

    trader = AutoTrader()
    trader.configure(
        run_scan_fn=lambda **_kwargs: {},
        kill_switch_fn=lambda: False,
        test_mode_fn=lambda: True,
        audit_db="audit.db",
        config_fn=lambda: {},
    )

    assert len(calls) == 1
