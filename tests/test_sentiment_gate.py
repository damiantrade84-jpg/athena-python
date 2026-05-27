import pytest
from types import SimpleNamespace
from datetime import datetime, timezone

import sentiment_gate
import config as config_module
from unittest.mock import patch

class DummyException(Exception):
    pass

class DummyAPIClient:
    def __init__(self, *args, **kwargs):
        pass

    def financial_news(self, s, limit, from_date, to_date):
        return self._responses.get(s, [])

@pytest.fixture
def mock_eodhd(monkeypatch):
    monkeypatch.setenv("EODHD_KEY", "dummy")
    try:
        import eodhd
        monkeypatch.setattr("eodhd.APIClient", DummyAPIClient)
    except ImportError:
        pass
    # Clear cache before each test
    sentiment_gate._cache.clear()

def test_sentiment_gate_threshold_long_blocked(mock_eodhd, monkeypatch):
    monkeypatch.setitem(config_module.CONFIG, "SENTIMENT_BLOCK_THRESHOLD", 0.4)
    
    client = DummyAPIClient()
    client._responses = {"BTC-USD.CC": [{"sentiment": {"polarity": -0.5}}]}
    try:
        import eodhd
        monkeypatch.setattr("eodhd.APIClient", lambda *a, **k: client)
    except ImportError:
        pass

    result = sentiment_gate.check_sentiment("BTC/USD", "LONG", "crypto")
    assert result["allowed"] is False
    assert result["score"] == -0.5
    assert "strongly bearish" in result["reason"]

def test_sentiment_gate_threshold_long_allowed(mock_eodhd, monkeypatch):
    monkeypatch.setitem(config_module.CONFIG, "SENTIMENT_BLOCK_THRESHOLD", 0.4)
    
    client = DummyAPIClient()
    client._responses = {"BTC-USD.CC": [{"sentiment": {"polarity": -0.3}}]}
    try:
        import eodhd
        monkeypatch.setattr("eodhd.APIClient", lambda *a, **k: client)
    except ImportError:
        pass

    result = sentiment_gate.check_sentiment("BTC/USD", "LONG", "crypto")
    assert result["allowed"] is True
    assert result["score"] == -0.3
    assert "strongly bearish" not in result["reason"]

def test_sentiment_gate_threshold_short_blocked(mock_eodhd, monkeypatch):
    monkeypatch.setitem(config_module.CONFIG, "SENTIMENT_BLOCK_THRESHOLD", 0.5)
    
    client = DummyAPIClient()
    client._responses = {"BTC-USD.CC": [{"sentiment": {"polarity": 0.6}}]}
    try:
        import eodhd
        monkeypatch.setattr("eodhd.APIClient", lambda *a, **k: client)
    except ImportError:
        pass

    result = sentiment_gate.check_sentiment("BTC/USD", "SHORT", "crypto")
    assert result["allowed"] is False
    assert result["score"] == 0.6
    assert "strongly bullish" in result["reason"]

def test_sentiment_gate_failure_policy_open(mock_eodhd, monkeypatch):
    monkeypatch.setattr(sentiment_gate, "_sentiment_api_fail_closed", lambda: False)

    def buggy_client(*args, **kwargs):
        raise DummyException("API offline")
    try:
        import eodhd
        monkeypatch.setattr("eodhd.APIClient", buggy_client)
    except ImportError:
        pass
    
    result = sentiment_gate.check_sentiment("BTC/USD", "LONG", "crypto")
    assert result["allowed"] is True

def test_sentiment_gate_failure_policy_closed(mock_eodhd, monkeypatch):
    monkeypatch.setattr(sentiment_gate, "_sentiment_api_fail_closed", lambda: True)

    def buggy_client(*args, **kwargs):
        raise DummyException("API offline")
    try:
        import eodhd
        monkeypatch.setattr("eodhd.APIClient", buggy_client)
    except ImportError:
        pass
    
    result = sentiment_gate.check_sentiment("BTC/USD", "LONG", "crypto")
    assert result["allowed"] is False
