"""Tests for alert-only suggested trade monitor."""

from __future__ import annotations

import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ai_review.suggested_trade_plan import sanitize_suggested_trade_plan
from suggested_trade_monitor import (
    DEFAULT_ACTIVE_PATH,
    DEFAULT_EVENTS_PATH,
    add_watch,
    build_watch_from_flag,
    cancel_watch,
    evaluate_trigger,
    evaluate_watches,
    validate_flag_payload,
)


def _valid_plan(**overrides):
    plan = {
        "schemaVersion": "suggested_trade_plan.v1",
        "armable": True,
        "source": "ai_chart_review",
        "symbol": "EURUSD",
        "direction": "SHORT",
        "action": "WAIT_FOR_LEVEL",
        "triggerType": "ACCEPTANCE_BELOW",
        "level": 1.085,
        "entryTf": "M15",
        "expiresInSeconds": 600,
    }
    plan.update(overrides)
    return plan


def test_sanitize_accepts_valid_level_plan():
    raw = {"suggestedTradePlan": _valid_plan()}
    out = sanitize_suggested_trade_plan(raw, source="ai_chart_review", symbol="EURUSD")
    assert out is not None
    assert out["armable"] is True
    assert out["level"] == 1.085


def test_sanitize_rejects_malformed_plan():
    raw = {"suggestedTradePlan": {"direction": "SIDEWAYS", "action": "WAIT_FOR_LEVEL", "triggerType": "ACCEPTANCE_ABOVE"}}
    out = sanitize_suggested_trade_plan(raw, source="ai_chart_review", symbol="EURUSD")
    assert out is not None
    assert out["armable"] is False


def test_validate_flag_rejects_entry_now():
    payload = {
        "symbol": "EURUSD",
        "suggestedTradePlan": _valid_plan(action="ENTRY_NOW"),
    }
    _, err = validate_flag_payload(payload)
    assert err is not None
    assert "ENTRY_NOW" in err or "cannot be watched" in err or "not watchable" in err or "invalid" in err.lower()


def test_validate_flag_rejects_no_trade():
    payload = {
        "symbol": "EURUSD",
        "suggestedTradePlan": _valid_plan(action="NO_TRADE"),
    }
    _, err = validate_flag_payload(payload)
    assert err is not None


def test_evaluate_trigger_acceptance_above():
    watch = {"trigger_type": "ACCEPTANCE_ABOVE", "level": 100.0}
    reached, _ = evaluate_trigger(watch, latest_close=101.0)
    assert reached is True


def test_evaluate_trigger_acceptance_below():
    watch = {"trigger_type": "ACCEPTANCE_BELOW", "level": 100.0}
    reached, _ = evaluate_trigger(watch, latest_close=99.0)
    assert reached is True


def test_evaluate_trigger_zone_touch():
    watch = {"trigger_type": "PULLBACK_TO_ZONE", "zone_low": 84.33, "zone_high": 84.84}
    reached, _ = evaluate_trigger(
        watch,
        latest_candle={"h": 84.5, "l": 84.4},
    )
    assert reached is True


def test_expiry_marks_watch_expired(monkeypatch):
    tmp_dir = Path(tempfile.mkdtemp(prefix="athena_suggested_expiry_"))
    active = tmp_dir / "active.json"
    events = tmp_dir / "events.jsonl"
    monkeypatch.setattr("suggested_trade_monitor.DEFAULT_ACTIVE_PATH", active)
    monkeypatch.setattr("suggested_trade_monitor.DEFAULT_EVENTS_PATH", events)

    validated, err = validate_flag_payload({
        "symbol": "EURUSD",
        "suggestedTradePlan": _valid_plan(expiresInSeconds=1),
        "createdAt": (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat(),
    })
    assert err is None
    watch, add_err = add_watch(validated, active_path=active, events_path=events)
    assert add_err is None
    assert watch is not None

    result = evaluate_watches(active_path=active, events_path=events)
    assert result["updated"] >= 1
    watches = json.loads(active.read_text(encoding="utf-8"))["watches"]
    assert watches[0]["status"] == "EXPIRED"


def test_monitor_module_has_no_execution_imports():
    source = Path(__file__).resolve().parents[1] / "suggested_trade_monitor.py"
    text = source.read_text(encoding="utf-8")
    forbidden = (
        "from execution",
        "import execution",
        "risk_engine",
        "guardian",
        "mt5_executor",
        "bybit_executor",
        "quick_execute",
        "scalp_execute",
    )
    for token in forbidden:
        assert token not in text
