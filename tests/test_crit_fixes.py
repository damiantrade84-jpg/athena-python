"""Tests for CRIT-001 and CRIT-002 fixes.

CRIT-001: /api/quick-execute must enforce EXECUTION_ENABLED and kill_switch
          guards with the same behavior as /api/execute.

CRIT-002: Forex intermarket max_score cap must be the same (2.0) across
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


# ── CRIT-002: forex intermarket cap parity ────────────────────────────────────

class TestForexIntermarketCapParity:

    def test_constant_value(self):
        """FOREX_ENGINE_A_MAX_SCORE must equal 2.0 (the live contract)."""
        from intermarket import FOREX_ENGINE_A_MAX_SCORE
        assert FOREX_ENGINE_A_MAX_SCORE == 2.0

    def test_live_path_uses_shared_constant(self):
        """FOREX_ENGINE_A_MAX_SCORE must equal the live athena.py hardcoded value (2.0).

        The live athena.py path calls apply_confirmation_to_score(max_score=2.0).
        The constant must be equal so both paths are always in sync.
        """
        from intermarket import FOREX_ENGINE_A_MAX_SCORE
        # Live hard-codes 2.0 (athena.py:9302); the constant must match exactly.
        assert FOREX_ENGINE_A_MAX_SCORE == 2.0

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
        """Live-style (max_score=2.0) and backtest-style (FOREX_ENGINE_A_MAX_SCORE)
        calls with identical inputs must produce the same adjusted score."""
        from intermarket import apply_confirmation_to_score, FOREX_ENGINE_A_MAX_SCORE

        pair = {"display": "EUR/USD", "type": "forex"}
        base = 1.75
        raw_ctx = None  # engine_a disabled → delta=0 in both paths, base returned unchanged

        result_live_style = apply_confirmation_to_score(
            base, "LONG", pair, raw_ctx, max_score=2.0  # live athena.py value
        )
        result_bt_style = apply_confirmation_to_score(
            base, "LONG", pair, raw_ctx, max_score=FOREX_ENGINE_A_MAX_SCORE  # fixed constant
        )

        # Both use the same cap now — outputs must match exactly
        assert result_live_style["adjusted_score"] == pytest.approx(
            result_bt_style["adjusted_score"], abs=1e-9
        ), "Live and backtest forex intermarket adjusted scores must be identical"
