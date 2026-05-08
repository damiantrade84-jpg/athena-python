"""Tests for CRIT-001 and CRIT-002 fixes.

CRIT-001: /api/quick-execute must enforce EXECUTION_ENABLED and kill_switch
          guards with the same behavior as /api/execute.

CRIT-002: Forex intermarket max_score cap must be the same (3.0) across
          live (athena.py) and backtest (backtest_runner.py) paths.
"""
import importlib
from unittest.mock import MagicMock, patch

import pytest


# ── CRIT-001 helpers ──────────────────────────────────────────────────────────

def _make_rt(execution_enabled: bool = True, kill_switch_active: bool = False):
    """Return a minimal runtime mock understood by api_quick_execute."""
    m = MagicMock()
    m.CONFIG = {
        "EXECUTION_ENABLED": execution_enabled,
        "SIGNAL_MAX_AGE_SEC": 300,
    }
    m.kill_switch.return_value = kill_switch_active
    m.ALL_PAIRS = []
    return m


def _app_client(execution_enabled: bool, kill_switch_active: bool = False):
    """Load a Flask test client with mocked runtime for execution.py routes."""
    import execution

    rt_mock = _make_rt(execution_enabled, kill_switch_active)

    with patch.object(execution, "rt", return_value=rt_mock):
        from flask import Flask
        app = Flask(__name__)
        execution.register_execution_routes(app)
        app.config["TESTING"] = True
        with app.test_client() as client:
            yield client


# ── CRIT-001: EXECUTION_ENABLED guard ────────────────────────────────────────

class TestQuickExecuteExecutionGuard:

    def test_quick_execute_blocked_when_disabled(self):
        """/api/quick-execute must return 403 when EXECUTION_ENABLED is false."""
        import execution

        rt_mock = _make_rt(execution_enabled=False)
        with patch.object(execution, "rt", return_value=rt_mock):
            from flask import Flask
            app = Flask(__name__)
            execution.register_execution_routes(app)
            app.config["TESTING"] = True
            client = app.test_client()

            resp = client.post(
                "/api/quick-execute",
                json={"signal": {"pair": "EUR/USD", "direction": "LONG"}},
            )

        assert resp.status_code == 403
        data = resp.get_json()
        assert "error" in data
        assert "disabled" in data["error"].lower() or "EXECUTION_ENABLED" in data["error"]

    def test_execute_blocked_when_disabled(self):
        """/api/execute must return 403 when EXECUTION_ENABLED is false (baseline)."""
        import execution

        rt_mock = _make_rt(execution_enabled=False)
        with patch.object(execution, "rt", return_value=rt_mock):
            from flask import Flask
            app = Flask(__name__)
            execution.register_execution_routes(app)
            app.config["TESTING"] = True
            client = app.test_client()

            resp = client.post(
                "/api/execute",
                json={"signal": {"pair": "EUR/USD", "direction": "LONG"}},
            )

        assert resp.status_code == 403
        # Both routes must return the same error message
        data = resp.get_json()
        assert "error" in data
        assert "disabled" in data["error"].lower() or "EXECUTION_ENABLED" in data["error"]

    def test_quick_execute_and_execute_same_disabled_message(self):
        """Both routes must return the identical error message when disabled."""
        import execution

        rt_mock = _make_rt(execution_enabled=False)
        with patch.object(execution, "rt", return_value=rt_mock):
            from flask import Flask
            app = Flask(__name__)
            execution.register_execution_routes(app)
            app.config["TESTING"] = True
            client = app.test_client()

            r_quick = client.post(
                "/api/quick-execute",
                json={"signal": {"pair": "EUR/USD", "direction": "LONG"}},
            )
            r_exec = client.post(
                "/api/execute",
                json={"signal": {"pair": "EUR/USD", "direction": "LONG"}},
            )

        assert r_quick.status_code == r_exec.status_code == 403
        assert r_quick.get_json()["error"] == r_exec.get_json()["error"]

    def test_quick_execute_blocked_by_kill_switch(self):
        """/api/quick-execute must return 503 when kill_switch is active."""
        import execution

        rt_mock = _make_rt(execution_enabled=True, kill_switch_active=True)
        with patch.object(execution, "rt", return_value=rt_mock):
            from flask import Flask
            app = Flask(__name__)
            execution.register_execution_routes(app)
            app.config["TESTING"] = True
            client = app.test_client()

            resp = client.post(
                "/api/quick-execute",
                json={"signal": {"pair": "EUR/USD", "direction": "LONG"}},
            )

        assert resp.status_code == 503
        data = resp.get_json()
        assert "kill" in data["error"].lower() or "Kill" in data["error"]

    def test_quick_execute_and_execute_same_kill_switch_message(self):
        """Both routes must return the identical error when kill_switch fires."""
        import execution

        rt_mock = _make_rt(execution_enabled=True, kill_switch_active=True)
        with patch.object(execution, "rt", return_value=rt_mock):
            from flask import Flask
            app = Flask(__name__)
            execution.register_execution_routes(app)
            app.config["TESTING"] = True
            client = app.test_client()

            r_quick = client.post(
                "/api/quick-execute",
                json={"signal": {"pair": "EUR/USD", "direction": "LONG"}},
            )
            r_exec = client.post(
                "/api/execute",
                json={"signal": {"pair": "EUR/USD", "direction": "LONG"}},
            )

        assert r_quick.status_code == r_exec.status_code == 503
        assert r_quick.get_json()["error"] == r_exec.get_json()["error"]

    def test_quick_execute_logs_empty_broker_failure(self, monkeypatch):
        """/api/quick-execute must not hide broker failures with no error field."""
        import execution
        import mt5_executor
        import risk_engine
        from flask import Flask

        rt_mock = _make_rt(execution_enabled=True)
        rt_mock.log = MagicMock()
        rt_mock.resolve_pair_from_signal = lambda *_args, **_kwargs: None
        rt_mock.fetch_candles = lambda *_args, **_kwargs: []
        rt_mock.calc_indicators_with_normalized = lambda *_args, **_kwargs: {}
        rt_mock.atr_for_levels = lambda *_args, **_kwargs: 0.001
        rt_mock.calc_levels = lambda *_args, **_kwargs: {}

        class _Approval:
            approved = True
            reason = "OK"
            volume = 0.1
            risk_amount = 10.0
            risk_pct = 0.001

            @staticmethod
            def to_dict():
                return {"approved": True, "reason": "OK"}

        monkeypatch.setattr(execution, "rt", lambda: rt_mock)
        monkeypatch.setattr(
            execution,
            "recompute_levels_for_style",
            lambda *_args, **_kwargs: {
                "pip_mode": "intraday",
                "atr": 0.001,
                "levels": {"sl": 1.09, "tp1": 1.12, "tp2": 1.13},
            },
        )
        monkeypatch.setattr(mt5_executor, "mt5_get_account", lambda: {"balance": 10000.0, "equity": 10000.0})
        monkeypatch.setattr(mt5_executor, "mt5_get_positions", lambda: {"positions": []})
        monkeypatch.setattr(mt5_executor, "mt5_get_symbol_info", lambda _symbol: {"digits": 5, "point": 0.00001})
        monkeypatch.setattr(risk_engine, "risk_check", lambda **_kwargs: _Approval())
        monkeypatch.setattr(execution, "_guardian_pre_trade", lambda *_args, **_kwargs: (True, "OK"))
        monkeypatch.setattr(
            execution,
            "run_managed_execution",
            lambda *_args, **_kwargs: {
                "success": False,
                "lifecycle": {
                    "phases": [{"name": "broker_execute", "success": False}]
                },
            },
        )

        app = Flask(__name__)
        execution.register_execution_routes(app)
        client = app.test_client()

        resp = client.post(
            "/api/quick-execute",
            json={
                "signal": {
                    "pair": "EUR/USD",
                    "display": "EUR/USD",
                    "type": "forex",
                    "direction": "LONG",
                    "price": 1.1,
                },
                "pip_mode": "intraday",
            },
        )

        data = resp.get_json()
        assert resp.status_code == 400
        assert data["error"] == "Execution failed: broker_execute returned no error detail"
        assert data["execution"]["error"] == data["error"]
        warning_messages = [str(call.args[0]) for call in rt_mock.log.warning.call_args_list]
        assert any("EUR/USD mt5 FAILED" in msg and data["error"] in msg for msg in warning_messages)


# Quick-execute freshness refresh
def test_quick_execute_refresh_replaces_stale_freshness_metadata(monkeypatch):
    import execution
    import mt5_executor
    import risk_engine
    from flask import Flask

    rt_mock = _make_rt(execution_enabled=True)
    rt_mock.ALL_PAIRS = [{"display": "EUR/USD"}]
    rt_mock.resolve_pair_from_signal = lambda *_args, **_kwargs: None
    rt_mock.fetch_candles = lambda *_args, **_kwargs: []
    rt_mock.calc_indicators_with_normalized = lambda *_args, **_kwargs: {}
    rt_mock.atr_for_levels = lambda *_args, **_kwargs: 0.001
    rt_mock.calc_levels = lambda *_args, **_kwargs: {}
    rt_mock.analyze_pair.return_value = {
        "pair": "EUR/USD",
        "direction": "LONG",
        "price": 1.101,
        "atr": 0.001,
        "confluenceScore": 2.5,
        "maxScore": 3.0,
        "trendState": "TRENDING",
        "timestamp": "2026-05-07T08:20:00+00:00",
        "candleFetchMeta": {"H4": {"stalenessSeverity": "fresh"}},
        "candleConsistency": {"H4": {"status": "CONFIRMED_ONLY_OK"}},
        "dataFreshness": {"allowed": True, "blocked": [], "reason": ""},
    }
    captured = {}

    class _Approval:
        approved = False
        reason = "TEST_STOP_AFTER_RISK_CAPTURE"

        @staticmethod
        def to_dict():
            return {"approved": False, "reason": "TEST_STOP_AFTER_RISK_CAPTURE"}

    def _fake_risk_check(**kwargs):
        captured["signal"] = dict(kwargs["signal"])
        return _Approval()

    monkeypatch.setattr(execution, "rt", lambda: rt_mock)
    monkeypatch.setattr(
        execution,
        "recompute_levels_for_style",
        lambda *_args, **_kwargs: {
            "pip_mode": "intraday",
            "atr": 0.001,
            "levels": {"sl": 1.09, "tp1": 1.12, "tp2": 1.13},
        },
    )
    monkeypatch.setattr(mt5_executor, "mt5_get_account", lambda: {"balance": 10000.0, "equity": 10000.0})
    monkeypatch.setattr(mt5_executor, "mt5_get_positions", lambda: {"positions": []})
    monkeypatch.setattr(mt5_executor, "mt5_get_symbol_info", lambda _symbol: {"digits": 5, "point": 0.00001})
    monkeypatch.setattr(risk_engine, "risk_check", _fake_risk_check)
    monkeypatch.setattr(execution, "insert_manual_error", lambda **_kwargs: None)

    app = Flask(__name__)
    execution.register_execution_routes(app)
    client = app.test_client()

    resp = client.post(
        "/api/quick-execute",
        json={
            "signal": {
                "pair": "EUR/USD",
                "display": "EUR/USD",
                "type": "forex",
                "direction": "LONG",
                "price": 1.1,
                "timestamp": "2000-01-01T00:00:00+00:00",
                "candleFetchMeta": {"H4": {"stalenessSeverity": "stale_multi_bucket"}},
            },
            "pip_mode": "intraday",
        },
    )

    assert resp.status_code == 400
    assert captured["signal"]["candleFetchMeta"]["H4"]["stalenessSeverity"] == "fresh"
    assert captured["signal"]["candleConsistency"]["H4"]["status"] == "CONFIRMED_ONLY_OK"
    assert captured["signal"]["dataFreshness"]["allowed"] is True


def test_execution_prefetch_candle_fetch_meta_updates_signal(monkeypatch):
    import execution

    calls: list[tuple[str, int]] = []

    def _fake_fetch(pair, tf, lim):
        calls.append((tf, int(lim)))
        return []

    mock_r = MagicMock()
    mock_r.CONFIG = {"QUICK_EXEC_PREFETCH_CANDLE_META": True}
    mock_r.ALL_PAIRS = [{"display": "USD/MXN", "source": "mt5", "type": "forex"}]
    mock_r.fetch_candles = _fake_fetch
    mock_r.log = MagicMock()

    monkeypatch.setattr(
        execution,
        "get_candle_fetch_meta",
        lambda _pair, tf, _lim: {"tf": tf, "lastBarStale": False, "stub": True},
    )
    monkeypatch.setattr(execution, "scan_candle_limits", lambda: {"H1": 11, "H4": 22, "D1": 33})

    sig = {
        "pair": "USD/MXN",
        "candleFetchMeta": {"H1": {"lastBarStale": True}},
    }
    execution._maybe_prefetch_execution_candle_fetch_meta(sig, _r=mock_r)

    assert calls == [("H1", 11), ("H4", 22), ("D1", 33)]
    assert sig["candleFetchMeta"]["H1"]["lastBarStale"] is False
    assert sig["candleFetchMeta"]["H1"]["stub"] is True
    assert sig["candleFetchMeta"]["pairSource"] == "mt5"


def test_execution_prefetch_disabled_is_noop():
    import execution

    calls = []

    mock_r = MagicMock()
    mock_r.CONFIG = {"QUICK_EXEC_PREFETCH_CANDLE_META": False}
    mock_r.ALL_PAIRS = [{"display": "EUR/USD"}]
    mock_r.fetch_candles = lambda *a, **k: calls.append(a) or []

    sig = {"pair": "EUR/USD"}
    execution._maybe_prefetch_execution_candle_fetch_meta(sig, _r=mock_r)

    assert calls == []


# ── CRIT-002: forex intermarket cap parity ────────────────────────────────────
class TestForexIntermarketCapParity:

    def test_constant_value(self):
        """FOREX_ENGINE_A_MAX_SCORE must equal 3.0 (Engine A v2 unified scale)."""
        from intermarket import FOREX_ENGINE_A_MAX_SCORE
        assert FOREX_ENGINE_A_MAX_SCORE == 3.0

    def test_live_path_uses_shared_constant(self):
        """FOREX_ENGINE_A_MAX_SCORE must equal the live athena.py value (3.0).

        Engine A v2 uses a unified 0-3.0 scale for all asset classes including forex.
        The constant must be equal so both paths are always in sync.
        """
        from intermarket import FOREX_ENGINE_A_MAX_SCORE
        # Engine A v2 unified 0-3.0 scale; the constant must match exactly.
        assert FOREX_ENGINE_A_MAX_SCORE == 3.0

        # Sanity: the old (wrong) backtest value was 1.0
        assert FOREX_ENGINE_A_MAX_SCORE != 1.0, (
            "FOREX_ENGINE_A_MAX_SCORE must not be 1.0 — that was the pre-fix backtest bug"
        )

    def test_backtest_imports_shared_constant(self):
        """backtest_runner must import FOREX_ENGINE_A_MAX_SCORE from intermarket."""
        import importlib.util, pathlib, ast

        bt_path = pathlib.Path(__file__).resolve().parents[1] / "backtest_runner.py"
        tree = ast.parse(bt_path.read_text(encoding="utf-8"))

        found = False
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module == "intermarket":
                    names = [alias.name for alias in node.names]
                    if "FOREX_ENGINE_A_MAX_SCORE" in names:
                        found = True
        assert found, "backtest_runner.py must import FOREX_ENGINE_A_MAX_SCORE from intermarket"

    def test_backtest_no_hardcoded_forex_1_0_cap(self):
        """backtest_runner.py must not contain max_score=1.0 in forex intermarket calls."""
        import pathlib

        bt_path = pathlib.Path(__file__).resolve().parents[1] / "backtest_runner.py"
        src = bt_path.read_text(encoding="utf-8")

        # All occurrences of max_score= in apply_confirmation_to_score calls should use
        # the constant, not the literal 1.0.
        # Use AST to only target apply_confirmation_to_score keyword arguments.
        import ast
        bt_path_ast = pathlib.Path(__file__).resolve().parents[1] / "backtest_runner.py"
        tree = ast.parse(bt_path_ast.read_text(encoding="utf-8"))
        violations = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            fname = func.id if isinstance(func, ast.Name) else (func.attr if isinstance(func, ast.Attribute) else "")
            if fname != "apply_confirmation_to_score":
                continue
            for kw in node.keywords:
                if kw.arg == "max_score" and isinstance(kw.value, ast.Constant) and kw.value.value == 1.0:
                    violations.append(getattr(node, "lineno", "?"))
        assert not violations, (
            f"apply_confirmation_to_score called with max_score=1.0 at lines {violations} — "
            "use FOREX_ENGINE_A_MAX_SCORE instead."
        )


    def test_parity_equivalent_inputs_live_and_backtest_style(self):
        """Live-style (max_score=3.0) and backtest-style (FOREX_ENGINE_A_MAX_SCORE)
        calls with identical inputs must produce the same adjusted score."""
        from intermarket import apply_confirmation_to_score, FOREX_ENGINE_A_MAX_SCORE

        pair = {"display": "EUR/USD", "type": "forex"}
        base = 1.75
        raw_ctx = None  # engine_a disabled → delta=0 in both paths, base returned unchanged

        result_live_style = apply_confirmation_to_score(
            base, "LONG", pair, raw_ctx, max_score=3.0  # Engine A v2 unified scale
        )
        result_bt_style = apply_confirmation_to_score(
            base, "LONG", pair, raw_ctx, max_score=FOREX_ENGINE_A_MAX_SCORE  # fixed constant
        )

        # Both use the same cap now — outputs must match exactly
        assert result_live_style["adjusted_score"] == pytest.approx(
            result_bt_style["adjusted_score"], abs=1e-9
        ), "Live and backtest forex intermarket adjusted scores must be identical"
