import pytest
from types import SimpleNamespace
from datetime import datetime, timezone

import event_risk
from config import CONFIG
from unittest.mock import patch

class DummyException(Exception):
    pass

@pytest.fixture
def clean_cache(monkeypatch):
    monkeypatch.setenv("EODHD_KEY", "dummy")
    event_risk._cache.clear()

def test_event_risk_failure_policy_open(clean_cache, monkeypatch):
    CONFIG["EVENT_RISK_API_FAIL_CLOSED"] = False

    def buggy_client(*args, **kwargs):
        raise DummyException("API offline")
    monkeypatch.setattr("event_risk.APIClient", buggy_client, raising=False)
    
    result = event_risk.check_event_risk("EUR/USD", "forex")
    assert result["allowed"] is True

    def crash_check(*args, **kwargs):
        raise DummyException("API totally failed")
    monkeypatch.setattr("event_risk.datetime", crash_check, raising=False)
    
    res = event_risk.check_event_risk("EUR/USD", "forex")
    assert res["allowed"] is True

def test_event_risk_failure_policy_closed(clean_cache, monkeypatch):
    CONFIG["EVENT_RISK_API_FAIL_CLOSED"] = True

    def crash_check(*args, **kwargs):
        raise DummyException("API totally failed")
    monkeypatch.setattr("event_risk.datetime", crash_check, raising=False)
    
    res = event_risk.check_event_risk("EUR/USD", "forex")
    assert res["allowed"] is False
