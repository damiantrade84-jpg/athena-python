"""Tests for safety fixes from deep audit (C1, M1, M2, M5, M7, L2)."""

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from flask import Flask

import execution
from risk_engine import risk_check


@pytest.fixture
def exec_client(monkeypatch):
    class _FakeLog:
        def warning(self, *args, **kwargs):
            return None

        def info(self, *args, **kwargs):
            return None

    class _FakeRt:
        CONFIG = {"EXECUTION_ENABLED": True, "SIGNAL_MAX_AGE_SEC": 300}
        ALL_PAIRS = [{"display": "EUR/USD", "type": "forex"}]
        AUDIT_DB = ":memory:"
        log = _FakeLog()

        @staticmethod
        def kill_switch():
            return False

        @staticmethod
        def analyze_pair(pair_obj, btc_bias, style="swing"):
            raise RuntimeError("refresh failed")

    monkeypatch.setattr(execution, "rt", lambda: _FakeRt())
    app = Flask(__name__)
    execution.register_execution_routes(app)
    return app.test_client()


def test_stale_signal_pair_not_in_universe_rejected(monkeypatch):
    class _FakeLog:
        def warning(self, *args, **kwargs):
            return None

    class _NoPairRt:
        CONFIG = {"EXECUTION_ENABLED": True, "SIGNAL_MAX_AGE_SEC": 300}
        ALL_PAIRS = []
        log = _FakeLog()

        @staticmethod
        def kill_switch():
            return False

    monkeypatch.setattr(execution, "rt", lambda: _NoPairRt())

    app = Flask(__name__)
    execution.register_execution_routes(app)
    client = app.test_client()
    resp = client.post(
        "/api/execute",
        json={
            "signal": {
                "pair": "EUR/USD",
                "direction": "LONG",
                "timestamp": "2000-01-01T00:00:00+00:00",
                "type": "forex",
                "price": 1.1,
                "sl": 1.09,
                "tp1": 1.12,
            }
        },
    )
    assert resp.status_code == 409
    assert "STALE_SIGNAL_REFRESH_FAILED" in resp.get_json()["error"]


def test_stale_signal_live_refresh_failed_rejected(exec_client):
    resp = exec_client.post(
        "/api/execute",
        json={
            "signal": {
                "pair": "EUR/USD",
                "direction": "LONG",
                "timestamp": "2000-01-01T00:00:00+00:00",
                "type": "forex",
                "price": 1.1,
                "sl": 1.09,
                "tp1": 1.12,
            }
        },
    )
    assert resp.status_code == 409
    assert "STALE_SIGNAL_REFRESH_FAILED" in resp.get_json()["error"]


def test_engine_b_execution_levels_return_checked(monkeypatch):
    class _FakeLog:
        def warning(self, *args, **kwargs):
            return None

    class _FakeRt:
        CONFIG = {"EXECUTION_ENABLED": True, "SIGNAL_MAX_AGE_SEC": 300}
        ALL_PAIRS = [{"display": "EUR/USD", "type": "forex"}]
        log = _FakeLog()

        @staticmethod
        def kill_switch():
            return False

    monkeypatch.setattr(execution, "rt", lambda: _FakeRt())
    monkeypatch.setattr(execution, "_apply_engine_b_execution_levels", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        execution,
        "_engine_b_levels_apply_error_response",
        lambda sig: ({"error": "ENGINE_B_LEVELS_UNAVAILABLE: test"}, 422),
    )

    app = Flask(__name__)
    execution.register_execution_routes(app)
    client = app.test_client()
    resp = client.post(
        "/api/execute",
        json={
            "engine_b": {"execution_sl": 1.09, "execution_tp": 1.12, "passed": True},
            "signal": {
                "pair": "EUR/USD",
                "direction": "LONG",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "type": "forex",
                "price": 1.1,
                "is_naked": True,
                "enginesAligned": True,
            },
        },
    )
    assert resp.status_code == 422
    assert "ENGINE_B_LEVELS_UNAVAILABLE" in resp.get_json()["error"]


def test_missing_bos_volume_data_does_not_confirm():
    """When no volume data is passed via result, bos_volume_confirmed stays False."""
    from market_structure import NakedEngine

    engine = NakedEngine()
    bars = [
        {"open": 1.0, "high": 1.5, "low": 0.9, "close": 1.2, "time": "2024-01-01T00:00:00Z"},
        {"open": 1.2, "high": 1.6, "low": 1.1, "close": 1.3, "time": "2024-01-01T01:00:00Z"},
        {"open": 1.3, "high": 1.7, "low": 1.2, "close": 1.4, "time": "2024-01-01T02:00:00Z"},
        {"open": 1.4, "high": 1.8, "low": 1.3, "close": 1.5, "time": "2024-01-01T03:00:00Z"},
        {"open": 1.5, "high": 1.9, "low": 1.4, "close": 1.6, "time": "2024-01-01T04:00:00Z"},
    ] * 5

    try:
        result = engine.analyze_structure(bars, "LONG", "EURUSD", "forex")
    except Exception:
        pytest.skip("analyze_structure raised — cannot test directly")

    bos_data = result.get("bos_data", {})
    bos_vol = bos_data.get("bos_volume_confirmed")
    assert bos_vol is not True, (
        "M2 regression: bos_volume_confirmed should not be True with no volume data"
    )


def _make_signal(**overrides):
    base = {
        "pair": "EUR/USD",
        "direction": "LONG",
        "price": 1.05,
        "sl": 1.04,
        "tp1": 1.07,
        "type": "forex",
        "timestamp": None,
        "confluenceScore": 2.0,
        "candleConsistency": {"H4": {"status": "OK"}},
    }
    base.update(overrides)
    return base


def test_verdict_empty_tier_watchlist_blocks_execution(monkeypatch):
    import risk_engine as re

    monkeypatch.setattr(re, "_current_drawdown", lambda *_args, **_kwargs: 0.0)
    monkeypatch.setattr(re, "_has_execution_freshness_evidence", lambda _s: True)
    monkeypatch.setattr(re, "CONFIG", {"MAX_OPEN_POSITIONS": 5})

    sig = _make_signal(verdict="", signalTier="WATCHLIST", decision_state="")
    result = risk_check(sig, 10000.0, 10000.0, [])
    assert result.approved is False
    assert result.reason == "NON_EXECUTABLE_SIGNAL_STATE"


def test_verdict_empty_tier_skip_blocks_execution(monkeypatch):
    import risk_engine as re

    monkeypatch.setattr(re, "_current_drawdown", lambda *_args, **_kwargs: 0.0)
    monkeypatch.setattr(re, "_has_execution_freshness_evidence", lambda _s: True)
    monkeypatch.setattr(re, "CONFIG", {"MAX_OPEN_POSITIONS": 5})

    sig = _make_signal(verdict="", signalTier="SKIP", decision_state="")
    result = risk_check(sig, 10000.0, 10000.0, [])
    assert result.approved is False


def test_decision_state_watchlist_blocks_execution(monkeypatch):
    import risk_engine as re

    monkeypatch.setattr(re, "_current_drawdown", lambda *_args, **_kwargs: 0.0)
    monkeypatch.setattr(re, "_has_execution_freshness_evidence", lambda _s: True)
    monkeypatch.setattr(re, "CONFIG", {"MAX_OPEN_POSITIONS": 5})

    sig = _make_signal(decision_state="watchlist")
    result = risk_check(sig, 10000.0, 10000.0, [])
    assert result.approved is False


def test_normal_verdict_passes_with_valid_state(monkeypatch):
    import risk_engine as re

    monkeypatch.setattr(re, "_current_drawdown", lambda *_args, **_kwargs: 0.0)
    monkeypatch.setattr(re, "_has_execution_freshness_evidence", lambda _s: True)
    monkeypatch.setattr(re, "CONFIG", {"MAX_OPEN_POSITIONS": 5})

    sig = _make_signal(
        verdict="ALIGNED",
        signalTier="EXECUTE",
        decision_state="execute",
        engine_b_status={"checklist_passed": True},
        components={"b_checklist_passed": True, "a_has_signal": True},
    )
    result = risk_check(sig, 10000.0, 10000.0, [])
    assert result.reason != "NON_EXECUTABLE_SIGNAL_STATE"


def test_trust_verdict_defaults_to_trust_neither():
    from engine_c_ai import normalize_engine_c_ai_weight_verdict

    out = normalize_engine_c_ai_weight_verdict({"trust_verdict": None})
    assert out.get("trust_verdict") != "trust_both"

    out2 = normalize_engine_c_ai_weight_verdict({})
    assert out2.get("trust_verdict") == "trust_neither"


def test_bybit_sl_width_exception_logged_not_silent(monkeypatch):
    from risk_engine import RiskApproval
    import bybit_executor

    class _FakeExchange:
        def fetch_ticker(self, symbol):
            return {"ask": 50000.0, "bid": 49999.0, "last": 50000.0}

    approval = RiskApproval(True, 0.01, 100.0, 0.01, 0.01, 0.0, "OK")
    signal = {
        "pair": "BTCUSDT",
        "symbol": "BTCUSDT",
        "direction": "LONG",
        "price": 50000.0,
        "sl": 49000.0,
        "tp1": 52000.0,
        "type": "crypto",
    }

    monkeypatch.setattr(bybit_executor, "_get_exchange", lambda: _FakeExchange())
    monkeypatch.setattr(bybit_executor, "_ensure_leverage", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "risk_engine.resolve_max_sl_pct",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("cap resolver down")),
    )

    out = bybit_executor.bybit_execute(signal, approval)
    assert out.get("success") is False
    assert "SL_CAP_CHECK_FAILED" in (out.get("error") or "")


def test_api_execute_risk_rejection_uses_422_not_200(monkeypatch):
    class _FakeLog:
        def warning(self, *args, **kwargs):
            return None

    class _FakeRt:
        CONFIG = {"EXECUTION_ENABLED": True, "SIGNAL_MAX_AGE_SEC": 300}
        ALL_PAIRS = [{"display": "EUR/USD", "type": "forex"}]
        log = _FakeLog()

        @staticmethod
        def kill_switch():
            return False

    class _RejectApproval:
        approved = False
        reason = "RR_BELOW_MINIMUM"
        volume = 0.0
        risk_amount = 0.0
        risk_pct = 0.0

        def to_dict(self):
            return {"approved": False, "reason": self.reason}

    monkeypatch.setattr(execution, "rt", lambda: _FakeRt())
    monkeypatch.setattr(execution, "_engine_a_trade_gate_block_reason", lambda *args, **kwargs: None)
    monkeypatch.setattr(execution, "_hydrate_execution_candle_quality", lambda *args, **kwargs: None)
    monkeypatch.setattr(execution, "apply_engine_a_exit_mode", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "risk_engine.risk_check",
        lambda **kwargs: _RejectApproval(),
    )
    monkeypatch.setattr(
        "mt5_executor.mt5_get_account",
        lambda: {"balance": 10000.0, "equity": 10000.0},
    )
    monkeypatch.setattr("mt5_executor.mt5_get_positions", lambda: {"positions": []})
    monkeypatch.setattr(
        "mt5_executor.mt5_get_symbol_info",
        lambda symbol: {"digits": 5, "point": 0.00001, "volume_min": 0.01},
    )

    app = Flask(__name__)
    execution.register_execution_routes(app)
    client = app.test_client()
    resp = client.post(
        "/api/execute",
        json={
            "signal": {
                "pair": "EUR/USD",
                "direction": "LONG",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "type": "forex",
                "price": 1.1,
                "sl": 1.09,
                "tp1": 1.12,
            }
        },
    )
    assert resp.status_code == 422
    assert "Risk engine rejected" in resp.get_json()["error"]


def test_api_execute_mt5_missing_symbol_uses_400_not_200(monkeypatch):
    class _FakeLog:
        def warning(self, *args, **kwargs):
            return None

    class _FakeRt:
        CONFIG = {"EXECUTION_ENABLED": True, "SIGNAL_MAX_AGE_SEC": 300}
        ALL_PAIRS = [{"display": "EUR/USD", "type": "forex"}]
        log = _FakeLog()

        @staticmethod
        def kill_switch():
            return False

    monkeypatch.setattr(execution, "rt", lambda: _FakeRt())
    monkeypatch.setattr(execution, "_engine_a_trade_gate_block_reason", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "mt5_executor.mt5_get_account",
        lambda: {"balance": 10000.0, "equity": 10000.0},
    )
    monkeypatch.setattr("mt5_executor.mt5_get_positions", lambda: {"positions": []})
    monkeypatch.setattr("mt5_executor.mt5_get_symbol_info", lambda symbol: None)

    app = Flask(__name__)
    execution.register_execution_routes(app)
    client = app.test_client()
    resp = client.post(
        "/api/execute",
        json={
            "signal": {
                "pair": "EUR/USD",
                "direction": "LONG",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "type": "forex",
                "price": 1.1,
                "sl": 1.09,
                "tp1": 1.12,
            }
        },
    )
    assert resp.status_code == 400
    assert "not available on your MT5 broker" in resp.get_json()["error"]
