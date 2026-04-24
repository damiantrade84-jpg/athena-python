from auto_trader import AutoTrader, _current_combined_conviction, _signal_expected_prob


def _base_cfg():
    return {
        "AUTO_TRADE_MIN_SCORE": {"crypto": 1.5, "forex": 1.6},
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


def test_can_execute_rechecks_conviction_after_debate_adjustment(monkeypatch):
    trader = AutoTrader()
    cfg = _base_cfg()
    cfg["SIGNAL_DEBATE_ENABLED"] = True

    monkeypatch.setattr(
        "signal_debate.run_signal_debate",
        lambda signal: {
            "grade": "WEAK_GO",
            "allowed": True,
            "reasoning": "trim conviction",
            "score_adjustment": -0.20,
        },
    )

    signal = {
        "pair": "BTC/USDT",
        "type": "crypto",
        "direction": "LONG",
        "trendState": "TRENDING",
        "regimeName": "TRENDING",
        "combinedConviction": 0.57,
        "confluenceScore": 0.95,
        "maxScore": 1.0,
    }
    ok, reason = trader._can_execute(signal, cfg)

    assert not ok
    assert reason == "debate-adjusted conviction 0.450 < min 0.550"
    assert signal["confluenceScore"] == 0.75
    assert signal["combinedConviction"] == 0.45


def test_signal_expected_prob_prefers_calibrated_probability_over_ui_threshold_progress():
    signal = {
        "calibratedProbability": 0.41,
        "combinedConviction": 0.72,
        "confluencePct": 100,
        "confluenceScore": 2.4,
        "maxScore": 3.0,
    }

    assert _signal_expected_prob(signal) == 0.41


def test_current_combined_conviction_uses_engine_a_only_contract_when_overlay_missing():
    signal = {
        "confluenceScore": 1.8,
        "maxScore": 3.0,
        "enginesAligned": False,
        "combinedConviction": None,
    }

    assert _current_combined_conviction(signal) == 0.36


def test_can_execute_rejects_when_debate_zeroes_engine_a_score(monkeypatch):
    trader = AutoTrader()
    cfg = _base_cfg()
    cfg["SIGNAL_DEBATE_ENABLED"] = True

    monkeypatch.setattr(
        "signal_debate.run_signal_debate",
        lambda signal: {
            "grade": "WEAK_GO",
            "allowed": True,
            "reasoning": "fully negate engine a",
            "score_adjustment": -0.95,
        },
    )

    signal = {
        "pair": "BTC/USDT",
        "type": "crypto",
        "direction": "LONG",
        "trendState": "TRENDING",
        "regimeName": "TRENDING",
        "combinedConviction": 0.57,
        "confluenceScore": 0.95,
        "maxScore": 1.0,
    }
    ok, reason = trader._can_execute(signal, cfg)

    assert not ok
    assert reason == "Debate reduced Engine A score to 0.0"
    assert signal["confluenceScore"] == 0
    assert signal["combinedConviction"] == 0.0


def test_run_auto_scan_skips_already_open_pair(monkeypatch):
    trader = AutoTrader()
    cfg = _base_cfg()
    signal = {
        "pair": "BTC/USDT",
        "type": "crypto",
        "direction": "LONG",
        "combinedConviction": 0.9,
        "confluenceScore": 2.7,
        "maxScore": 3.0,
        "timestamp": "2026-04-02T12:00:00+00:00",
    }

    trader.configure(
        lambda style="auto": {
            "success": True,
            "tradeSignals": [dict(signal)],
            "watchlist": [],
            "scanFunnel": {},
        },
        lambda: False,
        lambda: False,
        "",
        lambda: cfg,
    )

    monkeypatch.setattr(trader, "_can_execute", lambda sig, _cfg: (True, ""))
    monkeypatch.setattr(
        trader,
        "_get_cached_open_positions",
        lambda asset_type, cache: [{"pair": "BTC/USDT:USDT", "symbol": "BTC/USDT"}],
    )

    called = {"execute": False}

    def _fake_execute(sig, _cfg):
        called["execute"] = True
        return True

    monkeypatch.setattr(trader, "_execute_signal", _fake_execute)

    trader._run_auto_scan()

    assert called["execute"] is False


def test_run_auto_scan_skips_when_kill_switch_active():
    trader = AutoTrader()
    cfg = _base_cfg()
    called = {"scan": False}

    def _scan(style="auto"):
        called["scan"] = True
        return {"success": True, "tradeSignals": []}

    trader.configure(
        _scan,
        lambda: True,
        lambda: False,
        "",
        lambda: cfg,
    )

    trader._run_auto_scan()

    assert called["scan"] is False
