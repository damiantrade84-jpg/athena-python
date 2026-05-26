import json
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import auto_trader
from auto_trader import (
    AutoTrader,
    _set_execution_conviction_fields,
)
from config import CONFIG
from risk_engine import _signal_quality_factor


def _cfg(**overrides):
    base = {
        "AUTO_TRADE_MIN_CONVICTION": {"default": 0.55},
        "AUTO_TRADE_MAX_DAILY": 5,
        "AUTO_TRADE_MAX_PER_SCAN": 1,
        "AUTO_TRADE_SCHEDULER_MODE": "interval",
        "AUTO_TRADE_SESSIONS": {"crypto": ["always"], "forex": ["always"]},
        "AUTO_TRADE_BLOCKED_TREND_STATES": {"default": []},
        "AUTO_TRADE_BLOCKED_REGIMES": {"default": []},
        "EXECUTION_ENABLED": True,
        "MT5_EXECUTION_ENABLED": True,
        "BYBIT_EXECUTION_ENABLED": True,
        "SENTIMENT_GATE_ENABLED": False,
        "EVENT_RISK_ENABLED": False,
        "SIGNAL_DEBATE_ENABLED": False,
        "AUTO_TRADE_A_ONLY_WEIGHT": {"default": 0.60},
        "MAX_SL_PCT": {"crypto": 0.08, "forex": 0.025},
    }
    base.update(overrides)
    return base


def _signal(**overrides):
    data = {
        "pair": "BTC/USDT",
        "symbol": "BTCUSDT",
        "type": "crypto",
        "direction": "LONG",
        "trendState": "TRENDING",
        "regimeName": "TRENDING",
        "scoreNorm": 0.90,
        "combinedConviction": 0.90,
        "confluenceScore": 2.7,
        "maxScore": 3.0,
        "price": 50000.0,
        "sl": 49000.0,
        "tp1": 52000.0,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    data.update(overrides)
    return data


def _audit_db():
    path = tempfile.mktemp(suffix=".db")
    with sqlite3.connect(path, timeout=15.0) as con:
        con.execute(
            "CREATE TABLE IF NOT EXISTS audit_log ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "ts TEXT, pair TEXT, score REAL, engine TEXT, direction TEXT, "
            "asset_class TEXT, regime TEXT, entry_price REAL, sl REAL, tp REAL, "
            "volume REAL, risk_amount REAL, risk_pct REAL, ticket TEXT, "
            "grade TEXT, signal_price_ref REAL, slippage_bps REAL, "
            "max_score REAL, score_pct REAL, factors_json TEXT, "
            "edge_prob REAL, style TEXT, fee_cost REAL, error_tag TEXT"
            ")"
        )
        con.execute(
            "CREATE TABLE IF NOT EXISTS execution_events ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "ts TEXT, engine TEXT, success INTEGER, score REAL, "
            "max_score REAL, expected_prob REAL, slippage_bps REAL, meta_json TEXT)"
        )
    return path


def _approval():
    return SimpleNamespace(
        approved=True,
        reason=None,
        volume=0.01,
        risk_amount=10.0,
        risk_pct=0.01,
        meta={},
    )


def test_execution_enabled_false_blocks_before_broker_calls(monkeypatch):
    trader = AutoTrader()
    fake_mt5 = MagicMock()
    fake_mt5.mt5_get_account.side_effect = AssertionError("broker should not be called")
    monkeypatch.setitem(sys.modules, "mt5_executor", fake_mt5)

    sig = _signal(pair="EUR/USD", symbol="EURUSD", type="forex")
    ok = trader._execute_signal(sig, _cfg(EXECUTION_ENABLED=False))

    assert ok is False
    assert sig["autoDecisionTrace"]["blockReason"] == "AUTO_EXECUTION_DISABLED"
    fake_mt5.mt5_get_account.assert_not_called()


def test_mt5_and_bybit_switches_block_by_asset_before_broker(monkeypatch):
    trader = AutoTrader()
    fake_mt5 = MagicMock()
    fake_mt5.mt5_get_account.side_effect = AssertionError("mt5 should not be called")
    monkeypatch.setitem(sys.modules, "mt5_executor", fake_mt5)
    non_crypto = _signal(pair="EUR/USD", symbol="EURUSD", type="forex")

    assert trader._execute_signal(non_crypto, _cfg(MT5_EXECUTION_ENABLED=False)) is False
    assert non_crypto["autoDecisionTrace"]["blockReason"] == "MT5_EXECUTION_DISABLED"
    fake_mt5.mt5_get_account.assert_not_called()

    fake_bybit = MagicMock()
    fake_bybit.bybit_get_account.side_effect = AssertionError("bybit should not be called")
    monkeypatch.setitem(sys.modules, "bybit_executor", fake_bybit)
    crypto = _signal()

    assert trader._execute_signal(crypto, _cfg(BYBIT_EXECUTION_ENABLED=False)) is False
    assert crypto["autoDecisionTrace"]["blockReason"] == "BYBIT_EXECUTION_DISABLED"
    fake_bybit.bybit_get_account.assert_not_called()


def test_sl_too_wide_blocks_before_sentiment_event_conductor_and_debate(monkeypatch):
    trader = AutoTrader()
    calls = {"sentiment": 0, "event": 0, "debate": 0, "conductor": 0}

    monkeypatch.setattr(
        "sentiment_gate.check_sentiment",
        lambda *a, **k: calls.__setitem__("sentiment", calls["sentiment"] + 1),
    )
    monkeypatch.setattr(
        "event_risk.check_event_risk",
        lambda *a, **k: calls.__setitem__("event", calls["event"] + 1),
    )
    monkeypatch.setattr(
        "signal_debate.run_signal_debate",
        lambda *a, **k: calls.__setitem__("debate", calls["debate"] + 1),
    )
    monkeypatch.setattr(
        "conductor.conductor_orchestrate",
        lambda *a, **k: calls.__setitem__("conductor", calls["conductor"] + 1),
    )

    cfg = _cfg(
        SENTIMENT_GATE_ENABLED=True,
        AUTO_TRADE_SENTIMENT_GATE_MODE="always_when_enabled",
        EVENT_RISK_ENABLED=True,
        SIGNAL_DEBATE_ENABLED=True,
        MAX_SL_PCT={"crypto": 0.01},
    )
    sig = _signal(price=100.0, sl=80.0, tp1=110.0)

    ok, reason = trader._can_execute(sig, cfg)

    assert ok is False
    assert "SL distance" in reason
    assert calls == {"sentiment": 0, "event": 0, "debate": 0, "conductor": 0}


def test_ai_debate_penalty_does_not_mutate_engine_a_score(monkeypatch):
    trader = AutoTrader()
    cfg = _cfg(SIGNAL_DEBATE_ENABLED=True, AUTO_TRADE_MIN_CONVICTION={"default": 0.30})
    monkeypatch.setattr(
        "signal_debate.run_signal_debate",
        lambda signal: {
            "trace_id": "debate-1",
            "allowed": True,
            "grade": "WATCH",
            "score_adjustment": -0.30,
            "score_adjustment_raw": -0.30,
            "reasoning": "execution caution",
        },
    )
    monkeypatch.setattr(
        "ai_reconciliation.arbitrate_ai",
        lambda *a, **k: {"execution_allowed": True, "reason": "ok"},
    )
    sig = _signal(scoreNorm=0.70, combinedConviction=0.88, confluenceScore=2.1)
    before = (sig["confluenceScore"], sig["combinedConviction"], sig["scoreNorm"])

    ok, reason = trader._can_execute(sig, cfg)

    assert ok, reason
    assert (sig["confluenceScore"], sig["combinedConviction"], sig["scoreNorm"]) == before
    assert sig["aiDebatePenalty"] > 0
    assert sig["executionConvictionEffective"] < sig["executionConvictionBase"]


def test_ai_debate_arbitration_block_is_traceable(monkeypatch):
    trader = AutoTrader()
    cfg = _cfg(SIGNAL_DEBATE_ENABLED=True)
    monkeypatch.setattr(
        "signal_debate.run_signal_debate",
        lambda signal: {"trace_id": "debate-2", "allowed": False, "score_adjustment": 0.0},
    )
    monkeypatch.setattr(
        "ai_reconciliation.arbitrate_ai",
        lambda *a, **k: {"execution_allowed": False, "reason": "Debate rejected"},
    )

    ok, reason = trader._can_execute(_signal(), cfg)

    assert ok is False
    assert "AI arbitration" in reason


def test_ranking_uses_execution_rank_score_not_combined_conviction(monkeypatch):
    cfg = _cfg(AUTO_TRADE_A_ONLY_WEIGHT={"default": 0.80})
    weak_rank = _signal(pair="BTC/USDT", scoreNorm=0.60, combinedConviction=0.99)
    strong_rank = _signal(pair="ETH/USDT", symbol="ETHUSDT", scoreNorm=0.70, combinedConviction=0.10)
    executed = []

    trader = AutoTrader()
    trader.configure(
        lambda style="auto": {"success": True, "tradeSignals": [weak_rank, strong_rank]},
        lambda: False,
        lambda: True,
        "",
        lambda: cfg,
    )
    monkeypatch.setattr(trader, "_can_execute", lambda sig, _cfg: (True, ""))
    monkeypatch.setattr(trader, "_get_cached_open_positions", lambda *a, **k: [])
    monkeypatch.setattr(trader, "_execute_signal", lambda sig, _cfg: executed.append(sig["pair"]) or True)

    trader._run_auto_scan()

    assert executed == ["ETH/USDT"]
    assert strong_rank["executionRankScore"] > weak_rank["executionRankScore"]


def test_config_aware_rank_score_helper_uses_a_only_weight():
    sig = _signal(scoreNorm=0.75, confluenceScore=2.25, maxScore=3.0, enginesAligned=False)
    _set_execution_conviction_fields(sig, "engine_a", _cfg(AUTO_TRADE_A_ONLY_WEIGHT={"default": 0.70}))
    assert sig["executionRankScore"] == pytest.approx(0.525)


def test_risk_quality_prefers_execution_conviction_effective():
    signal = {"executionConvictionEffective": 0.42, "combinedConviction": 0.91}
    assert _signal_quality_factor(signal) == pytest.approx(0.42)


def test_conductor_receives_real_news_risk(monkeypatch):
    captured = {}

    def fake_orchestrate(*args, **kwargs):
        captured.update(kwargs)
        return {"routing": {"skip_signal": False, "run_debate": False}, "context": {}}

    monkeypatch.setattr("conductor.conductor_orchestrate", fake_orchestrate)
    monkeypatch.setattr("conductor.extract_conductor_microstructure", lambda signal: (None, None))
    trader = AutoTrader()
    trader._audit_db = tempfile.mktemp(suffix=".db")
    sig = _signal(
        majorEventRisk={
            "majorEventDetected": True,
            "reason": "FOMC within window",
            "majorEventDescription": "Fed decision",
        },
        newsSentimentSummary={"reasoningSummary": "rates risk"},
    )

    trader._hydrate_conductor_routing(sig)

    assert "FOMC within window" in captured["news_risk"]
    assert captured["news_ctx"]["reasoningSummary"] == "rates risk"
    assert sig["conductor"]["advisoryOnly"] is True
    assert sig["conductor"]["notUsedForExecution"] is True


def test_news_delta_already_applied_is_not_score_adjusted_again(monkeypatch):
    trader = AutoTrader()
    cfg = _cfg(
        SENTIMENT_GATE_ENABLED=True,
        AUTO_TRADE_SENTIMENT_GATE_MODE="always_when_enabled",
    )
    monkeypatch.setattr(
        "sentiment_gate.check_sentiment",
        lambda *a, **k: {"allowed": True, "reason": "clear"},
    )
    sig = _signal(
        confluenceScore=2.4,
        scoreAttribution={"newsSentimentDelta": -0.10, "finalEngineAScore": 2.4, "maxScore": 3.0},
    )

    ok, reason = trader._can_execute(sig, cfg)

    assert ok, reason
    assert sig["confluenceScore"] == 2.4
    assert sig["sentimentGate"]["newsScoreAlreadyApplied"] is True


def test_intermarket_severe_contradiction_blocks_only_when_configured():
    sig = _signal(intermarketConfirmation={"severeContradiction": True})
    ok, reason = AutoTrader()._can_execute(sig, _cfg(AUTO_TRADE_INTERMARKET_SEVERE_BLOCK=False))
    assert ok, reason

    blocked = _signal(intermarketConfirmation={"severeContradiction": True})
    ok, reason = AutoTrader()._can_execute(blocked, _cfg(AUTO_TRADE_INTERMARKET_SEVERE_BLOCK=True))
    assert ok is False
    assert reason == "INTERMARKET_SEVERE_CONTRADICTION"


def test_audit_rows_include_auto_decision_trace():
    path = _audit_db()
    trader = AutoTrader()
    trader.configure(None, None, lambda: True, path, lambda: {})
    sig = _signal(autoDecisionTrace={"traceId": "trace-ok", "finalAction": "execute"})
    try:
        trader._write_audit(sig, _approval(), {"success": True, "ticket": "T1"}, {})
        trader._write_error(_signal(autoDecisionTrace={"traceId": "trace-err"}), "TEST_BLOCK")
        with sqlite3.connect(path, timeout=15.0) as con:
            rows = con.execute(
                "SELECT grade, factors_json FROM audit_log ORDER BY id"
            ).fetchall()
        payloads = [json.loads(row[1]) for row in rows]
        assert payloads[0]["autoDecisionTrace"]["traceId"] == "trace-ok"
        assert payloads[1]["autoDecisionTrace"]["traceId"] == "trace-err"
    finally:
        try:
            import os

            os.unlink(path)
        except OSError:
            pass


def test_confirmed_close_mode_blocks_without_confirmed_candle():
    sig = _signal()
    ok, reason = AutoTrader()._can_execute(
        sig,
        _cfg(
            AUTO_TRADE_SCHEDULER_MODE="confirmed_close",
            AUTO_TRADE_ALLOW_INTRABAR_EXECUTION=False,
        ),
    )
    assert ok is False
    assert reason == "CONFIRMED_CANDLE_UNAVAILABLE"


def test_confirmed_close_mode_allows_fresh_confirmed_slot():
    now = datetime.now(timezone.utc)
    sig = _signal(lastConfirmedCandleTs=now.isoformat(), executionTimeframe="H4")

    ok, reason = AutoTrader()._can_execute(
        sig,
        _cfg(AUTO_TRADE_SCHEDULER_MODE="confirmed_close", AUTO_TRADE_EXECUTION_GRACE_MIN=5),
    )

    assert ok, reason


def test_confirmed_close_mode_blocks_mid_candle_outside_grace():
    stale = datetime.now(timezone.utc) - timedelta(hours=6)
    sig = _signal(lastConfirmedCandleTs=stale.isoformat(), executionTimeframe="H4")

    ok, reason = AutoTrader()._can_execute(
        sig,
        _cfg(
            AUTO_TRADE_SCHEDULER_MODE="confirmed_close",
            AUTO_TRADE_ALLOW_INTRABAR_EXECUTION=False,
            AUTO_TRADE_EXECUTION_GRACE_MIN=5,
        ),
    )

    assert ok is False
    assert reason == "CONFIRMED_CLOSE_WINDOW_MISSED"


def test_major_event_risk_blocks_when_blocks_auto_execution_set():
    sig = _signal(
        majorEventRisk={
            "majorEventDetected": True,
            "mode": "block_auto_execution",
            "action": "block_auto_execution",
            "blocksAutoExecution": True,
            "reason": "Emergency rate hike",
        },
    )

    ok, reason = AutoTrader()._can_execute(sig, _cfg())

    assert ok is False
    assert reason == "Emergency rate hike"
    assert sig.get("eventRiskGate", {}).get("source") == "majorEventRisk"


def test_chart_ai_gate_can_only_block_not_upgrade_or_mutate_score():
    fresh = datetime.now(timezone.utc).isoformat()
    low = _signal(
        scoreNorm=0.20,
        confluenceScore=0.6,
        aiChartReview={"timestamp": fresh, "human_action": "entry_now", "verdict": "approve"},
    )
    before = low["confluenceScore"]
    ok, reason = AutoTrader()._can_execute(
        low,
        _cfg(
            AUTO_TRADE_CHART_AI_GATE_ENABLED=True,
            AUTO_TRADE_CHART_AI_GATE_MODE="require_entry_now",
        ),
    )
    assert ok is False
    assert "0.200 < min" in reason
    assert low["confluenceScore"] == before

    reject = _signal(
        aiChartReview={"timestamp": fresh, "human_action": "wait", "verdict": "reject"},
    )
    ok, reason = AutoTrader()._can_execute(
        reject,
        _cfg(AUTO_TRADE_CHART_AI_GATE_ENABLED=True, AUTO_TRADE_CHART_AI_GATE_MODE="block_on_reject"),
    )
    assert ok is False
    assert reason == "AI_CHART_REJECT"
    assert reject["aiChartGate"]["scoreMutationEnabled"] is False


def test_tracked_auto_trade_config_defaults_are_live_safe():
    assert CONFIG["AUTO_TRADE_ENABLED"] is False
    assert CONFIG["AUTO_TRADE_MAX_DAILY"] == 2
    assert CONFIG["AUTO_TRADE_MAX_PER_SCAN"] == 1
    assert CONFIG["MAX_PORTFOLIO_HEAT"] == pytest.approx(0.06)
    assert CONFIG["MAX_OPEN_POSITIONS"] == 3
    assert CONFIG["MAX_CORRELATED_POSITIONS"] == 10
    assert CONFIG["AUTO_TRADE_SCHEDULER_MODE"] == "confirmed_close"
    assert CONFIG["CONDUCTOR"]["DYNAMIC_WEIGHTING_ENABLED"] is False
