from auto_trader import AutoTrader
import sys
from types import SimpleNamespace


def _base_cfg():
    return {
        "AUTO_TRADE_MIN_SCORE": {"crypto": 1.5, "forex": 0.7},
        "AUTO_TRADE_MIN_CONVICTION": {"default": 0.55},
        "AUTO_TRADE_MAX_DAILY": 3,
        "AUTO_TRADE_SESSIONS": {"crypto": ["always"]},
        "AUTO_TRADE_BLOCKED_TREND_STATES": {"default": [], "crypto": []},
        "AUTO_TRADE_BLOCKED_REGIMES": {"default": [], "crypto": []},
        "SENTIMENT_GATE_ENABLED": False,
        "EVENT_RISK_ENABLED": False,
        "SIGNAL_DEBATE_ENABLED": False,
    }


def test_can_execute_uses_live_min_conviction_gate():
    trader = AutoTrader()
    cfg = _base_cfg()

    low_signal = {
        "pair": "BTC/USDT",
        "type": "crypto",
        "direction": "LONG",
        "trendState": "TRENDING",
        "regimeName": "TRENDING",
        "combinedConviction": 0.54,
        "confluenceScore": 1.8,
        "maxScore": 3.0,
    }
    ok, reason = trader._can_execute(low_signal, cfg)
    assert not ok
    assert "conviction 0.540 < min 0.550" in reason

    high_signal = dict(low_signal, combinedConviction=0.56)
    ok, reason = trader._can_execute(high_signal, cfg)
    assert ok
    assert reason == ""


def test_status_reports_real_live_gate():
    trader = AutoTrader()
    cfg = _base_cfg()
    trader.configure(None, None, None, "", lambda: cfg)

    status = trader.get_status()

    assert status["scanMinScore"] == cfg["AUTO_TRADE_MIN_SCORE"]
    assert status["minScoreDeprecatedForExecution"] is True
    assert "Informational only" in status["minScoreExecutionNote"]
    assert status["minConviction"] == {"default": 0.55}
    assert status["liveGateMetric"] == "combinedConviction"
    assert "combinedConviction >= 0.55" in status["liveGateDisplay"]


def test_can_execute_rejects_when_meta_threshold_delta_raises_gate(monkeypatch):
    trader = AutoTrader()
    cfg = _base_cfg()

    monkeypatch.setattr(
        "auto_trader.predict_calibrated_prob",
        lambda *args, **kwargs: {
            "available": False,
            "calibrated_prob": 0.56,
            "fallback_reason": "test",
            "scope_used": None,
        },
    )
    monkeypatch.setattr(
        "auto_trader.meta_report",
        lambda *args, **kwargs: {
            "weights": {
                "engine_a": 0.30,
                "engine_b": 0.25,
                "engine_c": 0.35,
                "scalp": 0.10,
            },
            "thresholdAdjustments": {
                "engine_a": 0.03,
                "engine_b": 0.0,
                "engine_c": 0.0,
                "scalp": 0.0,
            },
            "suspensionFlags": {
                "engine_a": False,
                "engine_b": False,
                "engine_c": False,
                "scalp": False,
            },
        },
    )

    signal = {
        "pair": "BTC/USDT",
        "type": "crypto",
        "direction": "LONG",
        "trendState": "TRENDING",
        "regimeName": "TRENDING",
        "combinedConviction": 0.56,
        "confluenceScore": 1.8,
        "maxScore": 3.0,
    }
    ok, reason = trader._can_execute(signal, cfg)

    assert not ok
    assert reason == "conviction 0.560 < min 0.580"


def test_can_execute_rejects_when_meta_policy_suspends_bucket(monkeypatch):
    trader = AutoTrader()
    cfg = _base_cfg()

    monkeypatch.setattr(
        "auto_trader.predict_calibrated_prob",
        lambda *args, **kwargs: {
            "available": False,
            "calibrated_prob": 0.90,
            "fallback_reason": "test",
            "scope_used": None,
        },
    )
    monkeypatch.setattr(
        "auto_trader.meta_report",
        lambda *args, **kwargs: {
            "weights": {
                "engine_a": 0.22,
                "engine_b": 0.20,
                "engine_c": 0.48,
                "scalp": 0.10,
            },
            "thresholdAdjustments": {
                "engine_a": 0.0,
                "engine_b": 0.0,
                "engine_c": 0.0,
                "scalp": 0.0,
            },
            "suspensionFlags": {
                "engine_a": True,
                "engine_b": False,
                "engine_c": False,
                "scalp": False,
            },
        },
    )

    signal = {
        "pair": "BTC/USDT",
        "type": "crypto",
        "direction": "LONG",
        "trendState": "TRENDING",
        "regimeName": "TRENDING",
        "combinedConviction": 0.90,
        "confluenceScore": 2.7,
        "maxScore": 3.0,
    }
    ok, reason = trader._can_execute(signal, cfg)

    assert not ok
    assert reason == "meta policy suspended this engine bucket"


def test_can_execute_prefers_explicit_regime_name_over_trend_state_for_filtering():
    trader = AutoTrader()
    cfg = _base_cfg()
    cfg["AUTO_TRADE_BLOCKED_REGIMES"] = {"default": ["RANGING"]}

    signal = {
        "pair": "EUR/USD",
        "type": "forex",
        "direction": "LONG",
        "trendState": "TREND_PULLBACK",
        "regimeName": "RANGING",
        "combinedConviction": 0.9,
        "confluenceScore": 0.9,
        "maxScore": 1.0,
    }
    ok, reason = trader._can_execute(signal, cfg)

    assert not ok
    assert "TREND_PULLBACK/RANGING blocked" in reason


def test_can_execute_no_longer_calls_eodhd_sentiment_or_event_risk(monkeypatch):
    trader = AutoTrader()
    cfg = _base_cfg()
    cfg["SENTIMENT_GATE_ENABLED"] = True
    cfg["EVENT_RISK_ENABLED"] = True

    monkeypatch.setitem(
        sys.modules,
        "sentiment_gate",
        SimpleNamespace(
            check_sentiment=lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("sentiment gate should not be called")
            )
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "event_risk",
        SimpleNamespace(
            check_event_risk=lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("event risk gate should not be called")
            )
        ),
    )

    signal = {
        "pair": "EUR/USD",
        "type": "forex",
        "direction": "LONG",
        "trendState": "TREND_PULLBACK",
        "regimeName": "TRENDING",
        "combinedConviction": 0.9,
        "confluenceScore": 0.9,
        "maxScore": 1.0,
    }
    ok, reason = trader._can_execute(signal, cfg)

    assert ok
    assert reason == ""
