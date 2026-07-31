from pathlib import Path

from auto_trader import AutoTrader, _signal_engine


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


def test_live_host_restores_diagnostic_mode_after_backtest_blueprint_import():
    src = (Path(__file__).resolve().parents[1] / "athena.py").read_text(encoding="utf-8")
    assert '_diagnostic_mode_before_backtest_import = os.environ.get("ATHENA_DIAGNOSTIC_MODE")' in src
    assert 'os.environ.pop("ATHENA_DIAGNOSTIC_MODE", None)' in src
    assert 'os.environ["ATHENA_DIAGNOSTIC_MODE"] = _diagnostic_mode_before_backtest_import' in src

    fetcher_src = (
        Path(__file__).resolve().parents[1]
        / "athena_backtest"
        / "data"
        / "fetchers.py"
    ).read_text(encoding="utf-8")
    assert '_diagnostic_mode_before_load = os.environ.get("ATHENA_DIAGNOSTIC_MODE")' in fetcher_src
    assert 'os.environ["ATHENA_DIAGNOSTIC_MODE"] = _diagnostic_mode_before_load' in fetcher_src


def test_engine_a_v3_identity_beats_nested_engine_b_diagnostics():
    assert _signal_engine(
        {
            "engine": "ENGINE_A_V3",
            "contractVersion": "3.0",
            "naked_data": {"score": 4.0},
        }
    ) == "engine_a"
