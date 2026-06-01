from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from auto_trader import AutoTrader
from engine_a_trade_gate import (
    annotate_engine_a_trade_eligibility,
    apply_fail_closed_engine_a_trade_gate,
    engine_a_trade_enabled,
    engine_a_trade_gate_block_reason,
    resolve_engine_a_trade_eligibility,
)
from engine_c import normalise_engine_a
from risk_engine import risk_check


def _cfg(**overrides):
    base = {
        "ENGINE_A_TRADE_ELIGIBILITY_ENABLED": True,
        "ENGINE_A_TRADE_ENABLED_DEFAULT": False,
        "ENGINE_A_TRADE_ENABLED_BY_CLASS": {
            "forex": False,
            "commodity": False,
            "index": False,
            "stock": False,
            "crypto": False,
        },
        "ENGINE_A_TRADE_ENABLED_BY_SCORE_GROUP": {},
        "ENGINE_A_TRADE_ENABLED_OVERRIDES": {},
    }
    base.update(overrides)
    return base


def test_forex_major_is_research_only_by_class_gate():
    pair = {"display": "EUR/USD", "symbol": "EURUSD=X", "type": "forex"}

    detail = resolve_engine_a_trade_eligibility(pair, config=_cfg())

    assert detail["enabled"] is False
    assert detail["research_only"] is True
    assert detail["source"] == "class:forex"
    assert "research-only" in detail["reason"]


def test_display_override_can_enable_specific_evidence_qualified_pair():
    pair = {"display": "XAU/USD", "symbol": "XAUUSD=X", "type": "commodity"}
    cfg = _cfg(
        ENGINE_A_TRADE_ENABLED_OVERRIDES={
            "XAU/USD": True,
        }
    )

    detail = resolve_engine_a_trade_eligibility(pair, config=cfg)

    assert detail["enabled"] is True
    assert detail["research_only"] is False
    assert detail["source"] == "override:XAU/USD"
    assert engine_a_trade_enabled(pair, config=cfg) is True


def test_score_group_override_precedes_asset_class():
    pair = {
        "display": "BTC/USDT",
        "symbol": "BTCUSDT",
        "type": "crypto",
        "scoreGroup": "crypto_btc",
    }
    cfg = _cfg(
        ENGINE_A_TRADE_ENABLED_BY_CLASS={"crypto": False},
        ENGINE_A_TRADE_ENABLED_BY_SCORE_GROUP={"crypto_btc": True},
    )

    detail = resolve_engine_a_trade_eligibility(pair, config=cfg)

    assert detail["enabled"] is True
    assert detail["source"] == "score_group:crypto_btc"


def test_master_flag_off_preserves_legacy_trade_eligibility():
    pair = {"display": "EUR/USD", "symbol": "EURUSD=X", "type": "forex"}
    cfg = _cfg(ENGINE_A_TRADE_ELIGIBILITY_ENABLED=False)

    detail = resolve_engine_a_trade_eligibility(pair, config=cfg)

    assert detail["enabled"] is True
    assert detail["research_only"] is False
    assert detail["source"] == "master_disabled"


def test_annotation_marks_disabled_signal_without_changing_score_or_direction():
    pair = {"display": "EUR/USD", "symbol": "EURUSD=X", "type": "forex"}
    signal = {
        "direction": "LONG",
        "confluenceScore": 2.4,
        "warnings": [],
        "trade": True,
        "executable": True,
    }

    annotated = annotate_engine_a_trade_eligibility(signal, pair, config=_cfg())

    assert annotated is signal
    assert signal["direction"] == "LONG"
    assert signal["confluenceScore"] == 2.4
    assert signal["engineATradeEnabled"] is False
    assert signal["engineATradeGate"]["enabled"] is False
    assert signal["trade"] is False
    assert signal["executable"] is False
    assert any("ENGINE_A_RESEARCH_ONLY" in w for w in signal["warnings"])


def _engine_a_signal(**overrides):
    base = {
        "confluenceScore": 2.4,
        "maxScore": 3.0,
        "direction": "LONG",
    }
    base.update(overrides)
    return base


def test_engine_c_normalise_engine_a_blocks_explicit_false_trade_gate(monkeypatch):
    monkeypatch.setitem(__import__("config").CONFIG, "ENGINE_A_TRADE_ELIGIBILITY_ENABLED", True)
    norm = normalise_engine_a(
        _engine_a_signal(engineATradeEnabled=False, engineATradeGate={"reason": "research-only"})
    )
    assert norm["has_signal"] is False
    assert norm["has_partial_signal"] is False
    assert norm["trade_enabled"] is False


def test_engine_c_normalise_engine_a_blocks_missing_trade_gate_when_evidence_active(monkeypatch):
    monkeypatch.setitem(__import__("config").CONFIG, "ENGINE_A_TRADE_ELIGIBILITY_ENABLED", True)
    norm = normalise_engine_a(_engine_a_signal())
    assert norm["has_signal"] is False
    assert norm["has_partial_signal"] is False
    assert norm["trade_enabled"] is False


def test_engine_c_normalise_engine_a_missing_trade_gate_legacy_when_master_disabled(monkeypatch):
    monkeypatch.setitem(__import__("config").CONFIG, "ENGINE_A_TRADE_ELIGIBILITY_ENABLED", False)
    norm = normalise_engine_a(_engine_a_signal())
    assert norm["has_signal"] is True
    assert norm["trade_enabled"] is True


def test_engine_a_trade_gate_block_reason_blocks_explicit_false():
    sig = _engine_a_signal(
        pair="EUR/USD",
        type="forex",
        engineATradeEnabled=False,
        engineATradeGate={"reason": "research-only forex"},
    )
    reason = engine_a_trade_gate_block_reason(sig, config=_cfg())
    assert reason is not None
    assert reason.startswith("ENGINE_A_RESEARCH_ONLY:")


def test_engine_a_trade_gate_block_reason_resolves_missing_field_when_gate_active():
    sig = _engine_a_signal(pair="EUR/USD", display="EUR/USD", type="forex")
    reason = engine_a_trade_gate_block_reason(sig, config=_cfg())
    assert reason is not None
    assert "ENGINE_A_RESEARCH_ONLY" in reason


def test_engine_a_trade_gate_block_reason_ignores_engine_b_only_payload():
    sig = {
        "pair": "EUR/USD",
        "direction": "LONG",
        "engine": "engine_b",
        "verdict": "B_ONLY",
        "engineATradeEnabled": False,
        "recommended_stop_loss": 1.08,
        "recommended_take_profit": 1.12,
    }
    assert engine_a_trade_gate_block_reason(sig, config=_cfg()) is None


def test_apply_fail_closed_engine_a_trade_gate_sets_research_only_fields():
    signal = {"trade": True, "executable": True, "warnings": []}
    apply_fail_closed_engine_a_trade_gate(signal)
    assert signal["engineATradeEnabled"] is False
    assert signal["engineATradeGate"]["source"] == "error_closed"
    assert signal["trade"] is False
    assert signal["executable"] is False
    assert any("ENGINE_A_RESEARCH_ONLY" in w for w in signal["warnings"])


def test_athena_annotation_exception_uses_fail_closed_helper():
    src = (Path(__file__).resolve().parents[1] / "athena.py").read_text(encoding="utf-8")
    assert "apply_fail_closed_engine_a_trade_gate(signal)" in src


def test_risk_check_rejects_engine_a_research_only_even_when_trade_tier():
    approval = risk_check(
        {
            "pair": "EUR/USD",
            "direction": "LONG",
            "price": 1.1,
            "sl": 1.08,
            "tp1": 1.14,
            "type": "forex",
            "signalTier": "trade",
            "engineATradeEnabled": False,
        },
        account_balance=10000.0,
        account_equity=10000.0,
        open_positions=[],
    )
    assert approval.approved is False
    assert approval.reason == "ENGINE_A_RESEARCH_ONLY"


def test_auto_trader_can_execute_rejects_engine_a_research_only():
    trader = AutoTrader()
    cfg = {
        "AUTO_TRADE_MIN_CONVICTION": {"default": 0.55},
        "AUTO_TRADE_BLOCKED_TREND_STATES": {"default": []},
        "AUTO_TRADE_BLOCKED_REGIMES": {"default": []},
        "AUTO_TRADE_SESSIONS": {"crypto": ["always"]},
        "ENGINE_A_TRADE_ELIGIBILITY_ENABLED": True,
        "ENGINE_A_TRADE_ENABLED_DEFAULT": False,
        "ENGINE_A_TRADE_ENABLED_BY_CLASS": {"crypto": False},
        "ENGINE_A_TRADE_ENABLED_BY_SCORE_GROUP": {},
        "ENGINE_A_TRADE_ENABLED_OVERRIDES": {},
    }
    sig = {
        "pair": "BTC/USDT",
        "type": "crypto",
        "direction": "LONG",
        "trendState": "TRENDING",
        "regimeName": "TRENDING",
        "scoreNorm": 0.90,
        "confluenceScore": 2.7,
        "maxScore": 3.0,
        "price": 50000.0,
        "sl": 49000.0,
        "tp1": 52000.0,
        "engineATradeEnabled": False,
    }
    ok, reason = trader._can_execute(sig, cfg)
    assert ok is False
    assert "ENGINE_A_RESEARCH_ONLY" in reason


def test_auto_trader_can_execute_does_not_block_engine_b_only():
    trader = AutoTrader()
    cfg = {
        "AUTO_TRADE_MIN_CONVICTION": {"default": 0.55},
        "AUTO_TRADE_BLOCKED_TREND_STATES": {"default": []},
        "AUTO_TRADE_BLOCKED_REGIMES": {"default": []},
        "AUTO_TRADE_SESSIONS": {"forex": ["always"]},
        "ENGINE_A_TRADE_ELIGIBILITY_ENABLED": True,
    }
    sig = {
        "pair": "EUR/USD",
        "type": "forex",
        "direction": "LONG",
        "trendState": "TRENDING",
        "regimeName": "TRENDING",
        "combinedConviction": 0.90,
        "engine_b_conviction": 0.90,
        "executionConvictionEffective": 0.90,
        "price": 1.1,
        "sl": 1.08,
        "tp1": 1.14,
        "engine": "engine_b",
        "verdict": "B_ONLY",
        "engineATradeEnabled": False,
        "engine_b": {"passed": True, "recommended_stop_loss": 1.08, "recommended_take_profit": 1.14},
    }
    ok, reason = trader._can_execute(sig, cfg)
    assert "ENGINE_A_RESEARCH_ONLY" not in reason


def _execution_client(monkeypatch):
    import execution
    from flask import Flask

    rt_mock = MagicMock()
    rt_mock.CONFIG = {
        "EXECUTION_ENABLED": True,
        "SIGNAL_MAX_AGE_SEC": 300,
        "ENGINE_A_TRADE_ELIGIBILITY_ENABLED": True,
    }
    rt_mock.kill_switch.return_value = False
    rt_mock.ALL_PAIRS = []
    rt_mock.log = MagicMock()
    rt_mock.resolve_pair_from_signal = lambda *_args, **_kwargs: None
    rt_mock.fetch_candles = lambda *_args, **_kwargs: []
    rt_mock.calc_indicators_with_normalized = lambda *_args, **_kwargs: {}
    rt_mock.atr_for_levels = lambda *_args, **_kwargs: 0.001
    rt_mock.calc_levels = lambda *_args, **_kwargs: {}

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

    app = Flask(__name__)
    execution.register_execution_routes(app)
    app.config["TESTING"] = True
    return app.test_client(), execution


def test_quick_execute_blocks_engine_a_research_only_before_risk_check(monkeypatch):
    client, execution = _execution_client(monkeypatch)

    def _unexpected_risk_check(**_kwargs):
        raise AssertionError("risk_check must not run for research-only Engine A signal")

    monkeypatch.setattr("risk_engine.risk_check", _unexpected_risk_check)

    resp = client.post(
        "/api/quick-execute",
        json={
            "signal": {
                "pair": "EUR/USD",
                "display": "EUR/USD",
                "type": "forex",
                "direction": "LONG",
                "price": 1.1,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "confluenceScore": 2.4,
                "engineATradeEnabled": False,
            },
            "pip_mode": "intraday",
        },
    )
    data = resp.get_json()
    assert resp.status_code == 422
    assert "ENGINE_A_RESEARCH_ONLY" in data["error"]


def test_execute_blocks_engine_a_research_only_before_risk_check(monkeypatch):
    client, execution = _execution_client(monkeypatch)

    def _unexpected_risk_check(**_kwargs):
        raise AssertionError("risk_check must not run for research-only Engine A signal")

    monkeypatch.setattr("risk_engine.risk_check", _unexpected_risk_check)

    resp = client.post(
        "/api/execute",
        json={
            "signal": {
                "pair": "EUR/USD",
                "display": "EUR/USD",
                "type": "forex",
                "direction": "LONG",
                "price": 1.1,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "confluenceScore": 2.4,
                "engineATradeEnabled": False,
            }
        },
    )
    data = resp.get_json()
    assert resp.status_code == 422
    assert "ENGINE_A_RESEARCH_ONLY" in data["error"]


def test_quick_execute_allows_engine_b_only_payload_with_engine_a_gate_false(monkeypatch):
    import mt5_executor

    client, execution = _execution_client(monkeypatch)
    risk_called = {"count": 0}

    class _Approval:
        approved = True
        reason = "OK"
        volume = 0.1
        risk_amount = 10.0
        risk_pct = 0.001

        @staticmethod
        def to_dict():
            return {"approved": True, "reason": "OK"}

    def _risk_check(**_kwargs):
        risk_called["count"] += 1
        return _Approval()

    monkeypatch.setattr("risk_engine.risk_check", _risk_check)
    monkeypatch.setattr(mt5_executor, "mt5_get_account", lambda: {"balance": 10000.0, "equity": 10000.0})
    monkeypatch.setattr(mt5_executor, "mt5_get_positions", lambda: {"positions": []})
    monkeypatch.setattr(mt5_executor, "mt5_get_symbol_info", lambda _symbol: {"digits": 5, "point": 0.00001})
    monkeypatch.setattr(execution, "_guardian_pre_trade", lambda *_args, **_kwargs: (True, "OK"))
    monkeypatch.setattr(
        execution,
        "run_managed_execution",
        lambda *_args, **_kwargs: {"success": True, "ticket": "123", "volume": 0.1, "entryPrice": 1.1},
    )

    resp = client.post(
        "/api/quick-execute",
        json={
            "signal": {
                "pair": "EUR/USD",
                "display": "EUR/USD",
                "type": "forex",
                "direction": "LONG",
                "price": 1.1,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "engine": "engine_b",
                "verdict": "B_ONLY",
                "engineATradeEnabled": False,
                "is_naked": True,
                "naked_data": {
                    "passed": True,
                    "recommended_stop_loss": 1.08,
                    "recommended_take_profit": 1.12,
                },
            },
            "engine_b": {
                "passed": True,
                "recommended_stop_loss": 1.08,
                "recommended_take_profit": 1.12,
            },
            "pip_mode": "intraday",
        },
    )
    data = resp.get_json()
    assert risk_called["count"] == 1
    assert "ENGINE_A_RESEARCH_ONLY" not in str(data.get("error", ""))
