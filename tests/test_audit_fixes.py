import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
import os

# Test 1: Lottery Rule Consolidation
def test_lottery_rule_consolidation():
    from lottery_service import LOTTERY_GAME_SPECS
    from lottery_engine import LOTTERY_GAME_RULES
    
    # Verify they are now identical (aliased)
    assert LOTTERY_GAME_RULES == LOTTERY_GAME_SPECS
    assert "lotto" in LOTTERY_GAME_SPECS
    assert LOTTERY_GAME_SPECS["lotto"]["main_max"] == 58
    assert LOTTERY_GAME_SPECS["powerball"]["bonus_max"] == 20

# Test 2: Telegram DateTime Usage
def test_telegram_notify_datetime_usage():
    import telegram_notify
    from datetime import datetime
    
    # We can't easily check for warnings here without capturing logs, 
    # but we can verify the code uses timezone.utc
    with open("telegram_notify.py", "r", encoding="utf-8") as f:
        content = f.read()
        assert "datetime.now(timezone.utc)" in content
        assert "datetime.utcnow()" not in content

# Test 3: Scoring Gating Logic
def test_scoring_backtest_gating():
    from scoring import _classify_signal
    from config import CONFIG
    
    pair = {"display": "EUR/USD", "type": "forex", "enabled": True}
    signal = {
        "confluenceScore": 1.5,
        "scanThreshold": 1.0,
        "macroEventRisk": {"blocked": True, "reason": "NFP Imminent"},
        "eventRisk": {"hardBlock": False},
        "exchangeClosed": False,
        "scanDiagnostics": []
    }
    
    # 3a. Live mode (RESEARCH_MODE=False) -> Should block
    with patch.dict(CONFIG, {"RESEARCH_MODE": False, "BACKTEST_RUNNING": False, "ENGINE_A_TRADE_ELIGIBILITY_ENABLED": False}):
        tier, reason = _classify_signal(signal, pair)
        assert tier == "watchlist"
        assert "Blocked by Macro Event Gate" in reason

    # Process-global BACKTEST_RUNNING must not leak into live classification
    # unless the signal itself is explicitly marked as research/backtest.
    with patch.dict(CONFIG, {"RESEARCH_MODE": False, "BACKTEST_RUNNING": True, "BACKTEST_EVENT_RISK_GATING": False, "ENGINE_A_TRADE_ELIGIBILITY_ENABLED": False}):
        tier, reason = _classify_signal(signal, pair)
        assert tier == "watchlist"
        assert "Blocked by Macro Event Gate" in reason

    with patch.dict(CONFIG, {"RESEARCH_MODE": False, "BACKTEST_RUNNING": False, "BACKTEST_EVENT_RISK_GATING": False, "ENGINE_A_TRADE_ELIGIBILITY_ENABLED": False}):
        tier, reason = _classify_signal({**signal, "backtestRunning": True}, pair)
        assert tier == "trade"
        assert "Trade-ready" in reason
        
    # 3b. Backtest mode (RESEARCH_MODE=True), Gate OFF -> Should NOT block
    with patch.dict(CONFIG, {"RESEARCH_MODE": True, "BACKTEST_EVENT_RISK_GATING": False, "ENGINE_A_TRADE_ELIGIBILITY_ENABLED": False}):
        tier, reason = _classify_signal(signal, pair)
        assert tier == "trade"
        assert "Trade-ready" in reason

    # 3c. Backtest mode (RESEARCH_MODE=True), Gate ON -> Should block
    with patch.dict(CONFIG, {"RESEARCH_MODE": True, "BACKTEST_EVENT_RISK_GATING": True, "ENGINE_A_TRADE_ELIGIBILITY_ENABLED": False}):
        tier, reason = _classify_signal(signal, pair)
        assert tier == "watchlist"
        assert "Blocked by Macro Event Gate" in reason

# Test 4: Sentiment Gating Parity
def test_scoring_sentiment_gating():
    from scoring import _classify_signal
    from config import CONFIG
    
    pair = {"display": "BTC/USDT", "type": "crypto", "enabled": True}
    signal = {
        "confluenceScore": 2.0,
        "scanThreshold": 1.4,
        "sentimentBlocked": True,
        "eventRisk": {"hardBlock": False},
        "macroEventRisk": {"blocked": False},
        "exchangeClosed": False,
        "scanDiagnostics": []
    }
    
    # 4a. Live mode -> Should block
    with patch.dict(CONFIG, {"RESEARCH_MODE": False, "BACKTEST_RUNNING": False, "ENGINE_A_TRADE_ELIGIBILITY_ENABLED": False}):
        tier, reason = _classify_signal(signal, pair)
        assert tier == "watchlist"
        assert "Blocked by Sentiment Gate" in reason
        
    # 4b. Backtest mode, Gate OFF -> Should NOT block
    with patch.dict(CONFIG, {"RESEARCH_MODE": True, "BACKTEST_SENTIMENT_GATING": False, "ENGINE_A_TRADE_ELIGIBILITY_ENABLED": False}):
        tier, reason = _classify_signal(signal, pair)
        assert tier == "trade"
        assert "Trade-ready" in reason

    # 4c. Backtest mode, Gate ON -> Should block
    with patch.dict(CONFIG, {"RESEARCH_MODE": True, "BACKTEST_SENTIMENT_GATING": True, "ENGINE_A_TRADE_ELIGIBILITY_ENABLED": False}):
        tier, reason = _classify_signal(signal, pair)
        assert tier == "watchlist"
        assert "Blocked by Sentiment Gate" in reason


def test_classify_signal_confidence_gate_demotes_low_confidence():
    from scoring import _classify_signal
    from config import CONFIG

    pair = {"display": "BNB/USDT", "type": "crypto", "enabled": True}
    signal = {
        "confluenceScore": 2.68,
        "scanThreshold": 2.0,
        "confidence": 0.58,
        "eventRisk": {"hardBlock": False},
        "macroEventRisk": {"blocked": False},
        "exchangeClosed": False,
        "scanDiagnostics": [],
    }
    with patch.dict(
        CONFIG,
        {
            "ENGINE_A_TRADE_MIN_CONFIDENCE_ENABLED": True,
            "ENGINE_A_TRADE_MIN_CONFIDENCE": 0.60,
            "ENGINE_A_TRADE_ELIGIBILITY_ENABLED": False,
        },
    ):
        tier, reason = _classify_signal(signal, pair)
        assert tier == "watchlist"
        assert "confidence 0.58" in reason

    with patch.dict(
        CONFIG,
        {
            "ENGINE_A_TRADE_MIN_CONFIDENCE_ENABLED": False,
            "ENGINE_A_TRADE_ELIGIBILITY_ENABLED": False,
        },
    ):
        tier, reason = _classify_signal(signal, pair)
        assert tier == "trade"


def test_correlation_clusters_can_be_configured():
    from scoring import apply_correlation_cap
    from config import CONFIG

    signals = [{"pair": "AAA", "warnings": []}, {"pair": "BBB", "warnings": []}]
    with patch.dict(CONFIG, {"ENGINE_A_CORR_CLUSTERS": {"custom": ["AAA", "BBB"]}}):
        out = apply_correlation_cap(signals)

    assert out[1]["correlationWarning"] == "custom"


def test_get_min_confidence_threshold_resolves_score_group():
    from scoring import get_min_confidence_threshold
    from config import CONFIG

    pair = {"display": "LTC/USDT", "type": "crypto", "enabled": True}
    with patch.dict(
        CONFIG,
        {
            "ENGINE_A_SCORE_GROUP_MIN_CONFIDENCE": {
                "crypto_alt_majors": 0.63,
                "default": 0.55,
            },
            "ENGINE_A_PAIR_MIN_CONFIDENCE": {},
        },
    ):
        assert get_min_confidence_threshold(pair) == pytest.approx(0.63)


def test_classify_signal_confidence_gate_uses_score_group_minimum():
    from scoring import _classify_signal
    from config import CONFIG

    pair = {"display": "LTC/USDT", "type": "crypto", "enabled": True}
    signal = {
        "confluenceScore": 2.87,
        "scanThreshold": 2.0,
        "confidence": 0.6175,
        "eventRisk": {"hardBlock": False},
        "macroEventRisk": {"blocked": False},
        "exchangeClosed": False,
        "scanDiagnostics": [],
    }
    with patch.dict(
        CONFIG,
        {
            "ENGINE_A_TRADE_MIN_CONFIDENCE_ENABLED": True,
            "ENGINE_A_SCORE_GROUP_MIN_CONFIDENCE": {
                "crypto_alt_majors": 0.63,
                "default": 0.55,
            },
            "ENGINE_A_TRADE_ELIGIBILITY_ENABLED": False,
        },
    ):
        tier, reason = _classify_signal(signal, pair)
        assert tier == "watchlist"
        assert "crypto_alt_majors" in reason
        assert "0.63" in reason


def test_crypto_session_not_forex_off_hours_low():
    from scoring import get_session

    session = get_session("2026-06-06T20:00:00+00:00", asset_class="crypto", symbol="BNB/USDT")
    assert session["quality"] != "low"
