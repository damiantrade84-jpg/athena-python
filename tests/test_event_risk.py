import sys
from types import ModuleType

import event_risk
from config import CONFIG


def _install_eodhd(monkeypatch, client_cls):
    module = ModuleType("eodhd")
    module.APIClient = client_cls
    monkeypatch.setitem(sys.modules, "eodhd", module)


def test_event_risk_disabled_skips_clearly(monkeypatch):
    monkeypatch.setitem(CONFIG, "EVENT_RISK_ENABLED", False)
    monkeypatch.delenv("EODHD_KEY", raising=False)
    event_risk._cache.clear()

    result = event_risk.check_event_risk("EUR/USD", "forex")

    assert result["allowed"] is True
    assert result["eventRiskStatus"]["enabled"] is False
    assert result["eventRiskStatus"]["effectiveAction"] == "skipped_disabled"


def test_enabled_no_key_fail_open_marks_provider_unavailable(monkeypatch):
    monkeypatch.setitem(CONFIG, "EVENT_RISK_ENABLED", True)
    monkeypatch.setitem(CONFIG, "EVENT_RISK_API_FAIL_CLOSED", False)
    monkeypatch.delenv("EODHD_KEY", raising=False)
    event_risk._cache.clear()

    result = event_risk.check_event_risk("EUR/USD", "forex")

    assert result["allowed"] is True
    assert result["providerUnavailable"] is True
    assert result["eventRiskStatus"]["lastFetchStatus"] == "missing_api_key"
    assert result["eventRiskStatus"]["effectiveAction"] == "provider_unavailable_fail_open"


def test_enabled_no_key_fail_closed_blocks(monkeypatch):
    monkeypatch.setitem(CONFIG, "EVENT_RISK_ENABLED", True)
    monkeypatch.setitem(CONFIG, "EVENT_RISK_API_FAIL_CLOSED", True)
    monkeypatch.delenv("EODHD_KEY", raising=False)
    event_risk._cache.clear()

    result = event_risk.check_event_risk("EUR/USD", "forex")

    assert result["allowed"] is False
    assert result["eventRiskStatus"]["effectiveAction"] == "provider_unavailable_fail_closed"


def test_api_error_obeys_fail_policy(monkeypatch):
    class ErrorClient:
        def __init__(self, *_args, **_kwargs):
            pass

        def get_economic_events_data(self, *_args, **_kwargs):
            raise RuntimeError("API offline")

    _install_eodhd(monkeypatch, ErrorClient)
    monkeypatch.setenv("EODHD_KEY", "dummy")
    monkeypatch.setitem(CONFIG, "EVENT_RISK_ENABLED", True)
    event_risk._cache.clear()

    monkeypatch.setitem(CONFIG, "EVENT_RISK_API_FAIL_CLOSED", False)
    open_result = event_risk.check_event_risk("EUR/USD", "forex")
    assert open_result["allowed"] is True
    assert open_result["eventRiskStatus"]["effectiveAction"] == "provider_unavailable_fail_open"

    event_risk._cache.clear()
    monkeypatch.setitem(CONFIG, "EVENT_RISK_API_FAIL_CLOSED", True)
    closed_result = event_risk.check_event_risk("EUR/USD", "forex")
    assert closed_result["allowed"] is False
    assert closed_result["eventRiskStatus"]["effectiveAction"] == "provider_unavailable_fail_closed"


def test_high_impact_cpi_fomc_nfp_blocks(monkeypatch):
    class EventClient:
        def __init__(self, *_args, **_kwargs):
            pass

        def get_economic_events_data(self, *_args, **_kwargs):
            return [
                {
                    "date": "2099-01-01",
                    "time": "00:30:00",
                    "country": "US",
                    "event": "FOMC Rate Decision",
                    "importance": "high",
                }
            ]

    class FrozenDatetime:
        @staticmethod
        def now(tz=None):
            from datetime import datetime, timezone

            return datetime(2099, 1, 1, 0, 0, tzinfo=tz or timezone.utc)

        @staticmethod
        def fromisoformat(value):
            from datetime import datetime

            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))

        @staticmethod
        def strptime(value, fmt):
            from datetime import datetime

            return datetime.strptime(value, fmt)

    _install_eodhd(monkeypatch, EventClient)
    monkeypatch.setenv("EODHD_KEY", "dummy")
    monkeypatch.setitem(CONFIG, "EVENT_RISK_ENABLED", True)
    monkeypatch.setitem(CONFIG, "EVENT_RISK_API_FAIL_CLOSED", False)
    monkeypatch.setattr(event_risk, "datetime", FrozenDatetime)
    event_risk._cache.clear()

    result = event_risk.check_event_risk("EUR/USD", "forex", lookahead_hours=4)

    assert result["allowed"] is False
    assert "high-impact event" in result["reason"]
    assert result["eventRiskStatus"]["effectiveAction"] == "block_event_risk"
